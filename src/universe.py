from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any, Iterable

import httpx

from .db import Database
from .eodhd_client import EODHDClient

log = logging.getLogger(__name__)

SCREENER_CACHE_TTL = 24 * 60 * 60
INDEX_CACHE_TTL = 7 * 24 * 60 * 60  # index membership moves slowly
SCREENER_PAGE_LIMIT = 100

# ---------- universe choices ----------

UNIVERSE_ALL_INDICES = "all_indices"
UNIVERSE_SP500 = "sp500"
UNIVERSE_NDX = "ndx"
UNIVERSE_DJ30 = "dj30"
UNIVERSE_CUSTOM = "custom"
UNIVERSE_WATCHLIST = "watchlist"

UNIVERSE_LABELS: dict[str, str] = {
    UNIVERSE_ALL_INDICES: "All indices (S&P 500 + NASDAQ-100 + Dow 30)",
    UNIVERSE_SP500: "S&P 500",
    UNIVERSE_NDX: "NASDAQ-100",
    UNIVERSE_DJ30: "Dow 30",
    UNIVERSE_CUSTOM: "Custom screener",
    UNIVERSE_WATCHLIST: "My watchlist",
}

# The indices that feed "all_indices" and the individual index choices.
INDEX_CODES = (UNIVERSE_SP500, UNIVERSE_NDX, UNIVERSE_DJ30)

# Wikipedia constituent pages. Tables on these pages expose a Symbol/Ticker column
# which the scraper below locates by header name (robust to column-order drift).
WIKI_URLS: dict[str, str] = {
    UNIVERSE_SP500: "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    UNIVERSE_NDX:   "https://en.wikipedia.org/wiki/Nasdaq-100",
    UNIVERSE_DJ30:  "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
}

# ---------- screener tiers ----------

SCREENER_TIERS: dict[str, dict[str, Any]] = {
    "default": {
        "label": "Default — MCap >$1B, Price >$10, ADV >100k",
        "market_cap_min": 1_000_000_000,
        "price_min": 10.0,
        "avg_vol_min": 100_000,
    },
    "large_cap": {
        "label": "Large-cap — MCap >$10B, Price >$10, ADV >500k",
        "market_cap_min": 10_000_000_000,
        "price_min": 10.0,
        "avg_vol_min": 500_000,
    },
    "broad": {
        "label": "Broad — MCap >$500M, Price >$5, ADV >500k",
        "market_cap_min": 500_000_000,
        "price_min": 5.0,
        "avg_vol_min": 500_000,
    },
    "penny_friendly": {
        "label": "Penny-friendly — MCap >$100M, Price >$1, ADV >1M",
        "market_cap_min": 100_000_000,
        "price_min": 1.0,
        "avg_vol_min": 1_000_000,
    },
}


# ---------- ticker normalization ----------

def _normalize_ticker(raw: str) -> str:
    """EODHD codes look like AAPL.US. Class shares (BRK.B) use a dash: BRK-B.US."""
    raw = raw.strip().upper()
    if not raw:
        return ""
    if "." in raw and not raw.endswith(".US"):
        raw = raw.replace(".", "-")
    if not raw.endswith(".US"):
        raw = f"{raw}.US"
    return raw


def _row_to_screener(row: dict[str, Any]) -> dict[str, Any]:
    code = row.get("code") or row.get("Code") or row.get("ticker") or ""
    return {
        "ticker": _normalize_ticker(code),
        "market_cap": row.get("market_capitalization") or row.get("MarketCapitalization"),
        "last_price": row.get("adjusted_close") or row.get("AdjustedClose"),
        "avg_daily_vol": row.get("avgvol_5d") or row.get("AvgVol5D"),
    }


# ---------- Wikipedia scraper ----------

_TICKER_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,5}")
_SYMBOL_HEADERS = {"symbol", "ticker", "ticker symbol", "code"}


class _WikiTickerParser(HTMLParser):
    """
    Walk every ``wikitable`` table on a Wikipedia page. For each one, locate a
    Symbol/Ticker column by header text and collect that column's value from
    every subsequent row. Tables without a recognized header are skipped (some
    pages — Nasdaq-100 — open with summary tables that aren't constituents).

    Nested tables are ignored: we only consume content at depth 1 inside the
    current wikitable so a sub-table inside a cell can't bleed into output.
    """

    def __init__(self) -> None:
        super().__init__()
        self.in_wikitable = False
        self.nesting = 0
        self.symbol_col: int | None = None  # reset per wikitable
        self.cell_index = -1
        self.cell_buf: list[str] = []
        self.in_th = False
        self.in_td = False
        self.tickers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k: v for k, v in attrs}
        if tag == "table":
            cls = attrs_d.get("class", "") or ""
            if not self.in_wikitable and "wikitable" in cls:
                self.in_wikitable = True
                self.nesting = 1
                self.symbol_col = None  # rediscover header for this table
            elif self.in_wikitable:
                self.nesting += 1
            return
        if not self.in_wikitable or self.nesting != 1:
            return
        if tag == "tr":
            self.cell_index = -1
        elif tag in ("th", "td"):
            self.cell_index += 1
            self.cell_buf = []
            self.in_th = tag == "th"
            self.in_td = tag == "td"

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.in_wikitable:
            self.nesting -= 1
            if self.nesting == 0:
                self.in_wikitable = False
            return
        if not self.in_wikitable or self.nesting != 1:
            return
        if tag in ("th", "td"):
            text = " ".join("".join(self.cell_buf).split()).strip()
            if self.in_th and self.symbol_col is None:
                if text.lower() in _SYMBOL_HEADERS:
                    self.symbol_col = self.cell_index
            elif (
                self.in_td
                and self.symbol_col is not None
                and self.cell_index == self.symbol_col
            ):
                token = text.split()[0] if text else ""
                m = _TICKER_RE.fullmatch(token)
                if m:
                    self.tickers.append(token)
            self.in_th = False
            self.in_td = False

    def handle_data(self, data: str) -> None:
        # Only buffer text at the outermost wikitable level — nested tables
        # are ignored so their cell content doesn't bleed into the ticker column.
        if (self.in_th or self.in_td) and self.in_wikitable and self.nesting == 1:
            self.cell_buf.append(data)


def parse_wiki_tickers(html: str) -> list[str]:
    p = _WikiTickerParser()
    p.feed(html)
    # dedupe, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in p.tickers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


async def _scrape_index(http: httpx.AsyncClient, index_code: str) -> list[dict[str, str]]:
    url = WIKI_URLS[index_code]
    resp = await http.get(url, follow_redirects=True, timeout=20.0)
    resp.raise_for_status()
    tickers = parse_wiki_tickers(resp.text)
    return [{"ticker": _normalize_ticker(t), "company_name": None} for t in tickers]


async def ensure_index_members(
    db: Database, http: httpx.AsyncClient, index_code: str
) -> list[str]:
    """
    Returns EODHD-form tickers for an index (sp500/ndx/dj30). Refreshes from Wikipedia
    when the cache is stale; falls back to whatever is cached if the scrape fails.
    """
    age = await db.index_age_seconds(index_code)
    stale = age is None or age > INDEX_CACHE_TTL
    if stale:
        try:
            rows = await _scrape_index(http, index_code)
            # Sanity floors — don't overwrite a good cache with a degraded scrape.
            min_expected = {UNIVERSE_SP500: 400, UNIVERSE_NDX: 80, UNIVERSE_DJ30: 20}
            if len(rows) >= min_expected[index_code]:
                await db.replace_index_members(index_code, rows)
                log.info("index %s refreshed: %d tickers", index_code, len(rows))
            else:
                log.warning(
                    "index %s scrape returned only %d rows; keeping cache",
                    index_code, len(rows),
                )
        except Exception as e:
            log.exception("index %s scrape failed: %s", index_code, e)
    return await db.get_index_tickers(index_code)


# ---------- screener tier caching ----------

async def _refresh_screener_tier(
    db: Database, client: EODHDClient, tier: str
) -> int:
    spec = SCREENER_TIERS[tier]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await client.screener(
            market_cap_min=spec["market_cap_min"],
            price_min=spec["price_min"],
            avg_vol_min=spec["avg_vol_min"],
            limit=SCREENER_PAGE_LIMIT,
            offset=offset,
        )
        if not page:
            break
        rows.extend(_row_to_screener(r) for r in page)
        if len(page) < SCREENER_PAGE_LIMIT:
            break
        offset += SCREENER_PAGE_LIMIT
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for r in rows:
        if r["ticker"] and r["ticker"] not in seen:
            seen.add(r["ticker"])
            cleaned.append(r)
    await db.replace_screener_tier(tier, cleaned)
    log.info("screener tier %s refreshed: %d tickers", tier, len(cleaned))
    return len(cleaned)


async def ensure_screener_tier(
    db: Database, client: EODHDClient, tier: str
) -> list[str]:
    if tier not in SCREENER_TIERS:
        tier = "default"
    age = await db.screener_age_seconds(tier)
    if age is None or age > SCREENER_CACHE_TTL:
        try:
            await _refresh_screener_tier(db, client, tier)
        except Exception as e:
            log.exception("screener refresh failed for tier %s: %s", tier, e)
    return await db.get_screener_tickers(tier)


# ---------- user-universe resolution ----------

async def resolve_user_universe(
    db: Database,
    client: EODHDClient,
    http: httpx.AsyncClient,
    *,
    choice: str,
    tier: str,
    chat_id: int,
) -> list[str]:
    """Map (choice, tier, chat_id) → list of EODHD tickers for a single user."""
    if choice == UNIVERSE_WATCHLIST:
        rows = await db.list_watch(chat_id)
        return [r["ticker"] for r in rows]
    if choice == UNIVERSE_CUSTOM:
        return await ensure_screener_tier(db, client, tier)
    if choice in INDEX_CODES:
        return await ensure_index_members(db, http, choice)
    # all_indices — default
    out: list[str] = []
    seen: set[str] = set()
    for code in INDEX_CODES:
        for t in await ensure_index_members(db, http, code):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


async def resolve_union_for_users(
    db: Database,
    client: EODHDClient,
    http: httpx.AsyncClient,
    users: Iterable[Any],
) -> list[str]:
    """Deduplicated ticker union across every user's resolved universe."""
    union: set[str] = set()
    for user in users:
        try:
            tickers = await resolve_user_universe(
                db, client, http,
                choice=user["universe_choice"],
                tier=user["screener_tier"],
                chat_id=user["chat_id"],
            )
        except Exception as e:
            log.exception("resolve failed for chat=%s: %s", user["chat_id"], e)
            continue
        union.update(tickers)
    return sorted(union)
