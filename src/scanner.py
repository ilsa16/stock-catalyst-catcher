from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .eodhd_client import EODHDClient

log = logging.getLogger(__name__)

QUOTE_BATCH_SIZE = 100  # EODHD recommends ~100 symbols per Live v2 call
ABSOLUTE_GAP_FLOOR = 5.0  # global lower bound; per-user threshold filters further


@dataclass(frozen=True)
class GapHit:
    ticker: str  # e.g. AAPL.US
    price: float
    prior_close: float
    gap_pct: float
    timestamp: int | None  # EODHD epoch seconds, may be None

    @property
    def display_ticker(self) -> str:
        return self.ticker.split(".")[0]


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str) and v.upper() == "NA":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def parse_quote(raw: dict[str, Any]) -> GapHit | None:
    """Parse one Live v2 quote dict into a GapHit, or None if invalid/below floor."""
    code = raw.get("code") or raw.get("Code")
    price = _to_float(raw.get("close"))
    prior = _to_float(raw.get("previousClose"))
    change_p = _to_float(raw.get("change_p"))

    if not code or price is None or prior is None or prior <= 0:
        return None

    # Prefer change_p from the API; fall back to computed.
    gap_pct = change_p if change_p is not None else (price - prior) / prior * 100.0

    if gap_pct < ABSOLUTE_GAP_FLOOR:
        return None

    ts = raw.get("timestamp")
    try:
        ts_int = int(ts) if ts is not None else None
    except (TypeError, ValueError):
        ts_int = None

    return GapHit(
        ticker=str(code).upper(),
        price=price,
        prior_close=prior,
        gap_pct=gap_pct,
        timestamp=ts_int,
    )


async def scan_universe(client: EODHDClient, tickers: list[str]) -> list[GapHit]:
    """
    Pull batched Live v2 quotes for `tickers`, parse, filter to >= ABSOLUTE_GAP_FLOOR,
    and return hits sorted by gap_pct descending.
    """
    hits: list[GapHit] = []
    for i in range(0, len(tickers), QUOTE_BATCH_SIZE):
        batch = tickers[i : i + QUOTE_BATCH_SIZE]
        try:
            quotes = await client.live_batch(batch)
        except Exception as e:  # don't let one batch nuke the run
            log.exception("quote batch failed (offset=%d, size=%d): %s", i, len(batch), e)
            continue
        for q in quotes:
            hit = parse_quote(q)
            if hit is not None:
                hits.append(hit)

    hits.sort(key=lambda h: h.gap_pct, reverse=True)
    return hits
