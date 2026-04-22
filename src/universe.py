from __future__ import annotations

import logging
from typing import Any

from .db import Database
from .eodhd_client import EODHDClient

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 24 * 60 * 60  # one trading day is enough for a fundamentals-driven screen
SCREENER_PAGE_LIMIT = 100  # EODHD screener max per page


def _normalize_ticker(raw: str) -> str:
    """EODHD codes look like AAPL.US — keep that form everywhere."""
    raw = raw.strip().upper()
    if "." not in raw:
        raw = f"{raw}.US"
    return raw


def _row_to_universe(row: dict[str, Any]) -> dict[str, Any]:
    """Map screener row → universe_cache row. Tolerates EODHD column name drift."""
    code = row.get("code") or row.get("Code") or row.get("ticker") or ""
    return {
        "ticker": _normalize_ticker(code),
        "market_cap": row.get("market_capitalization") or row.get("MarketCapitalization"),
        "last_price": row.get("adjusted_close") or row.get("AdjustedClose"),
        "avg_daily_vol": row.get("avgvol_5d") or row.get("AvgVol5D"),
    }


async def refresh_universe(
    db: Database,
    client: EODHDClient,
    *,
    market_cap_min: float,
    price_min: float,
    avg_vol_min: float,
) -> int:
    """Pull fresh screener pages and rewrite universe_cache. Returns ticker count."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await client.screener(
            market_cap_min=market_cap_min,
            price_min=price_min,
            avg_vol_min=avg_vol_min,
            limit=SCREENER_PAGE_LIMIT,
            offset=offset,
        )
        if not page:
            break
        rows.extend(_row_to_universe(r) for r in page)
        if len(page) < SCREENER_PAGE_LIMIT:
            break
        offset += SCREENER_PAGE_LIMIT

    # Drop dupes / blanks
    seen: set[str] = set()
    cleaned = []
    for r in rows:
        if r["ticker"] and r["ticker"] not in seen:
            seen.add(r["ticker"])
            cleaned.append(r)

    await db.replace_universe(cleaned)
    log.info("universe refreshed: %d tickers", len(cleaned))
    return len(cleaned)


async def ensure_universe(
    db: Database,
    client: EODHDClient,
    *,
    override: list[str] | None = None,
    market_cap_min: float,
    price_min: float,
    avg_vol_min: float,
) -> list[str]:
    """
    Returns the active ticker list. Uses override if provided; otherwise refreshes
    the screener cache when stale and returns the cached list.
    """
    if override:
        tickers = [_normalize_ticker(t) for t in override]
        log.info("using override universe: %d tickers", len(tickers))
        return tickers

    age = await db.universe_age_seconds()
    if age is None or age > CACHE_TTL_SECONDS:
        await refresh_universe(
            db,
            client,
            market_cap_min=market_cap_min,
            price_min=price_min,
            avg_vol_min=avg_vol_min,
        )
    return await db.get_universe_tickers()
