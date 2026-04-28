from __future__ import annotations

import logging
from typing import Any, Iterable

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

INDEX_CODES = (UNIVERSE_SP500, UNIVERSE_NDX, UNIVERSE_DJ30)

# EODHD index symbols. Constituents fetched via the fundamentals endpoint
# (`/fundamentals/{symbol}.INDX`). Replaces the earlier Wikipedia scrape: same
# output shape, but it's a single first-party API, no UA/HTML brittleness, and
# membership stays in sync with whatever EODHD's screener thinks "US" means.
EODHD_INDEX_SYMBOLS: dict[str, str] = {
    UNIVERSE_SP500: "GSPC",
    UNIVERSE_NDX:   "NDX",
    UNIVERSE_DJ30:  "DJI",
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
        # Screener field renamed by EODHD: avgvol_5d → avgvol_1d (see eodhd_client).
        "avg_daily_vol": (
            row.get("avgvol_1d")
            or row.get("AvgVol1D")
            or row.get("avgvol_5d")
            or row.get("AvgVol5D")
        ),
    }


# ---------- index membership (EODHD-backed) ----------

async def ensure_index_members(
    db: Database, client: EODHDClient, index_code: str
) -> list[str]:
    """
    Return EODHD-form tickers for an index (sp500/ndx/dj30). Refresh the local
    cache via EODHD's fundamentals endpoint when stale; on any failure, fall
    back to whatever was last cached.
    """
    if index_code not in EODHD_INDEX_SYMBOLS:
        return []
    age = await db.index_age_seconds(index_code)
    if age is None or age > INDEX_CACHE_TTL:
        try:
            raw = await client.index_constituents(EODHD_INDEX_SYMBOLS[index_code])
            min_expected = {UNIVERSE_SP500: 400, UNIVERSE_NDX: 80, UNIVERSE_DJ30: 20}
            if len(raw) >= min_expected[index_code]:
                rows = [
                    {"ticker": _normalize_ticker(r["code"]), "company_name": r.get("name")}
                    for r in raw
                    if r.get("code")
                ]
                await db.replace_index_members(index_code, rows)
                log.info("index %s refreshed: %d tickers", index_code, len(rows))
            else:
                log.warning(
                    "index %s constituents call returned only %d rows; keeping cache",
                    index_code, len(raw),
                )
        except Exception as e:
            log.exception("index %s constituents fetch failed: %s", index_code, e)
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
        return await ensure_index_members(db, client, choice)
    # all_indices — default
    out: list[str] = []
    seen: set[str] = set()
    for code in INDEX_CODES:
        for t in await ensure_index_members(db, client, code):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


async def resolve_union_for_users(
    db: Database,
    client: EODHDClient,
    users: Iterable[Any],
) -> list[str]:
    """Deduplicated ticker union across every user's resolved universe."""
    union: set[str] = set()
    for user in users:
        try:
            tickers = await resolve_user_universe(
                db, client,
                choice=user["universe_choice"],
                tier=user["screener_tier"],
                chat_id=user["chat_id"],
            )
        except Exception as e:
            log.exception("resolve failed for chat=%s: %s", user["chat_id"], e)
            continue
        union.update(tickers)
    return sorted(union)
