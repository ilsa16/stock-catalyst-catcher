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


def _normalize_ticker(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    # Some terminals/clients autolink dotted symbols into markdown like
    # "[AAPL.US](http://AAPL.US)" — strip that defensively.
    if s.startswith("[") and "](" in s and s.endswith(")"):
        s = s[1 : s.index("]")]
    s = s.upper()
    return s or None


def parse_quote(raw: dict[str, Any], *, now: float | None = None) -> GapHit | None:
    """
    Parse one Live v2 (us-quote-delayed) row into a GapHit, or None if invalid /
    below the absolute floor.

    Logic:
      - Use the extended-hours print (`ethPrice` / `ethTime`) iff it's both
        within ETH_FRESH_SECONDS *and* more recent than the latest regular print.
        That makes pre-market scans use eth and regular-hours scans use the
        live regular session, automatically.
      - gap_pct is always (price - prior_close) / prior_close * 100; we don't
        trust the API's `changePercent` field — its denominator varies per row
        in observed responses.
    """
    code = _normalize_ticker(raw.get("code") or raw.get("symbol") or raw.get("Code"))
    if not code:
        return None

    prior = _to_float(raw.get("previousClosePrice") or raw.get("previousClose"))
    if prior is None or prior <= 0:
        return None

    now_s = now if now is not None else time.time()

    # Extended-hours print
    eth_price = _to_float(raw.get("ethPrice"))
    eth_time_ms = _to_int(raw.get("ethTime"))

    # Regular-session last print. Prefer lastTradeTime (ms) if present, else
    # fall back to top-level timestamp (seconds).
    reg_price = _to_float(raw.get("lastTradePrice") or raw.get("close"))
    reg_time_ms = _to_int(raw.get("lastTradeTime"))
    if reg_time_ms is None:
        ts_s = _to_int(raw.get("timestamp"))
        reg_time_ms = ts_s * 1000 if ts_s is not None else None

    eth_fresh = (
        eth_price is not None
        and eth_time_ms is not None
        and 0 <= now_s - eth_time_ms / 1000.0 <= ETH_FRESH_SECONDS
    )
    use_eth = eth_fresh and (
        reg_time_ms is None
        or (eth_time_ms is not None and eth_time_ms > reg_time_ms)
    )

    if use_eth:
        price = eth_price  # type: ignore[assignment]
        ts: int | None = eth_time_ms // 1000  # type: ignore[operator]
        source = "extended"
    else:
        if reg_price is None:
            return None
        price = reg_price
        ts = reg_time_ms // 1000 if reg_time_ms is not None else None
        source = "regular"

    gap_pct = (price - prior) / prior * 100.0
    if gap_pct < ABSOLUTE_GAP_FLOOR:
        return None

    return GapHit(
        ticker=code,
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
