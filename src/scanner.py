from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .eodhd_client import EODHDClient

log = logging.getLogger(__name__)

QUOTE_BATCH_SIZE = 100  # ~100 symbols per Live v2 call
ABSOLUTE_GAP_FLOOR = 5.0  # global lower bound; per-user threshold filters further

# Extended-hours print is preferred when its timestamp is within this window.
# Keeps us from picking up yesterday's after-hours close at 4:30 AM ET.
ETH_FRESH_SECONDS = 6 * 3600


@dataclass(frozen=True)
class GapHit:
    ticker: str  # e.g. AAPL.US
    price: float
    prior_close: float
    gap_pct: float
    timestamp: int | None  # epoch seconds, may be None
    source: str  # "extended" | "regular"

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


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_quote(raw: dict[str, Any], *, now: float | None = None) -> GapHit | None:
    """
    Parse one Live v2 (us-quote-delayed) row into a GapHit, or None if invalid /
    below the absolute floor.

    Prefers the extended-hours print when `ethPrice` is set and `ethTime` is
    within the last ETH_FRESH_SECONDS. Otherwise falls back to the regular
    session `close` / `change_p`.
    """
    code = raw.get("code") or raw.get("Code")
    prior = _to_float(raw.get("previousClose"))
    if not code or prior is None or prior <= 0:
        return None

    now_s = now if now is not None else time.time()

    eth_price = _to_float(raw.get("ethPrice"))
    eth_time_ms = _to_int(raw.get("ethTime"))

    use_eth = (
        eth_price is not None
        and eth_time_ms is not None
        and 0 <= now_s - eth_time_ms / 1000.0 <= ETH_FRESH_SECONDS
    )

    if use_eth:
        price = eth_price  # type: ignore[assignment]
        gap_pct = (price - prior) / prior * 100.0
        ts: int | None = eth_time_ms // 1000  # type: ignore[operator]
        source = "extended"
    else:
        price = _to_float(raw.get("close"))
        if price is None:
            return None
        change_p = _to_float(raw.get("change_p"))
        gap_pct = change_p if change_p is not None else (price - prior) / prior * 100.0
        ts = _to_int(raw.get("timestamp"))
        source = "regular"

    if gap_pct < ABSOLUTE_GAP_FLOOR:
        return None

    return GapHit(
        ticker=str(code).upper(),
        price=price,
        prior_close=prior,
        gap_pct=gap_pct,
        timestamp=ts,
        source=source,
    )


async def scan_universe(client: EODHDClient, tickers: list[str]) -> list[GapHit]:
    """
    Pull batched Live v2 quotes for `tickers`, parse, filter to
    >= ABSOLUTE_GAP_FLOOR, return hits sorted by gap_pct descending.
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
    if hits:
        ext = sum(1 for h in hits if h.source == "extended")
        log.info("scan: %d hits (%d extended, %d regular)", len(hits), ext, len(hits) - ext)
    return hits
