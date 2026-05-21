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

# Default upper bound on a hit's age. Anything older is considered stale and
# dropped from the digest — stops the bot re-emitting yesterday's gaps as if
# they're new. Caller (scan_universe) can override per-call; the default of 12h
# is set so a /run_now run during late post-market still shows that day's close.
DEFAULT_MAX_HIT_AGE_SECONDS = 24 * 3600

# Gap baseline: which "starting price" to compute the gap percentage against.
#   GAP_VS_PREV_CLOSE — current price vs yesterday's regular-session close.
#     Captures overnight gappers in pre-market and total-day moves in post-market.
#   GAP_VS_TODAY_OPEN — current price vs today's regular-session open. The
#     trader-conventional "intraday gap": how much the stock has moved since
#     today's open print. Used during regular hours to surface intraday breakouts.
GAP_VS_PREV_CLOSE = "vs_prev_close"
GAP_VS_TODAY_OPEN = "vs_today_open"


@dataclass(frozen=True)
class GapHit:
    ticker: str  # e.g. AAPL.US
    price: float
    prior_close: float
    gap_pct: float
    timestamp: int | None  # epoch seconds, may be None
    source: str  # "extended" | "regular"
    # Extracted from the quote response; carried through so the per-user
    # filter in jobs.daily_scan can apply the screener tier on top of any
    # universe (not just Custom). None when EODHD didn't return the field.
    market_cap: float | None = None
    avg_volume: float | None = None

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
    # "[AAPL.US](http://AAPL.US)" — strip that defensively when ingesting.
    if s.startswith("[") and "](" in s and s.endswith(")"):
        s = s[1 : s.index("]")]
    s = s.upper()
    return s or None


def parse_quote(
    raw: dict[str, Any],
    *,
    now: float | None = None,
    max_age_seconds: int | None = None,
    gap_baseline: str = GAP_VS_PREV_CLOSE,
) -> GapHit | None:
    """
    Parse one Live v2 (us-quote-delayed) row into a GapHit, or None if invalid,
    below the absolute floor, or staler than `max_age_seconds`.

    Logic:
      - Use the extended-hours print (`ethPrice` / `ethTime`) iff it's both
        within ETH_FRESH_SECONDS *and* more recent than the latest regular print.
        That makes pre-market/post-market scans use eth and regular-hours scans
        use the live regular session, automatically.
      - gap_pct denominator is selected by `gap_baseline`:
          GAP_VS_PREV_CLOSE → previousClosePrice (yesterday's regular close)
          GAP_VS_TODAY_OPEN → today's open price; falls back to previousClose
                              when open isn't populated (early pre-market).
        We don't trust the API's `change_p` / `changePercent` — its denominator
        varies per row in observed responses.
      - Falls back to the older /real-time response shape (`close`,
        `previousClose`, seconds-resolution `timestamp`) so that historic
        respx fixtures and any cached payloads still parse.
      - When `max_age_seconds` is set, drops the hit if its chosen timestamp is
        older than that many seconds from `now`. Default None = no filter, so
        unit tests with hard-coded timestamps still pass; callers like
        scan_universe pass DEFAULT_MAX_HIT_AGE_SECONDS.
    """
    code = _normalize_ticker(raw.get("code") or raw.get("symbol") or raw.get("Code"))
    if not code:
        return None

    prev_close = _to_float(raw.get("previousClosePrice") or raw.get("previousClose"))
    today_open = _to_float(raw.get("open"))
    if gap_baseline == GAP_VS_TODAY_OPEN and today_open is not None and today_open > 0:
        baseline = today_open
    else:
        baseline = prev_close
    if baseline is None or baseline <= 0:
        return None

    now_s = now if now is not None else time.time()

    # Extended-hours print
    eth_price = _to_float(raw.get("ethPrice"))
    eth_time_ms = _to_int(raw.get("ethTime"))

    # Regular-session last print. v2 uses `lastTradePrice` + `lastTradeTime`
    # (ms). Older /real-time used `close` + top-level `timestamp` (seconds).
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

    gap_pct = (price - baseline) / baseline * 100.0
    if gap_pct < ABSOLUTE_GAP_FLOOR:
        return None

    if max_age_seconds is not None and ts is not None:
        if now_s - ts > max_age_seconds:
            return None

    return GapHit(
        ticker=code,
        price=price,
        prior_close=baseline,
        gap_pct=gap_pct,
        timestamp=ts,
        source=source,
        market_cap=_to_float(raw.get("marketCap")),
        avg_volume=_to_float(raw.get("averageVolume")),
    )


def _overlay_ws_quotes(
    raw_quotes: list[dict[str, Any]],
    ws_results: dict[str, dict[str, Any]],
) -> None:
    """
    Mutate `raw_quotes` in-place: for each ticker present in `ws_results`
    (real-time WS trade), replace lastTradePrice/lastTradeTime if the WS
    timestamp is more recent than the HTTP snapshot. parse_quote then
    naturally picks the fresher print.

    Tickers absent from ws_results are left as-is, so the HTTP value still
    works as a fallback when WS sees no trade.
    """
    for q in raw_quotes:
        code = (q.get("code") or q.get("symbol") or q.get("Code") or "")
        code = str(code).upper()
        ws = ws_results.get(code)
        if ws is None:
            continue
        http_ts = q.get("lastTradeTime") or 0
        try:
            http_ts_int = int(http_ts)
        except (TypeError, ValueError):
            http_ts_int = 0
        if ws["ts_ms"] > http_ts_int:
            q["lastTradePrice"] = ws["price"]
            q["lastTradeTime"] = ws["ts_ms"]


async def scan_universe(
    client: EODHDClient,
    tickers: list[str],
    *,
    max_age_seconds: int | None = DEFAULT_MAX_HIT_AGE_SECONDS,
    ws_collect_seconds: float = 0.0,
    ws_api_key: str | None = None,
    gap_baseline: str = GAP_VS_PREV_CLOSE,
) -> list[GapHit]:
    """
    Pull batched Live v2 quotes for `tickers`, parse, filter to
    >= ABSOLUTE_GAP_FLOOR and at most `max_age_seconds` old, return hits sorted
    by gap_pct descending.

    When `ws_collect_seconds > 0` and `ws_api_key` is set, also opens an
    EODHD WebSocket trade collector for the same universe and overlays any
    fresher prints onto the HTTP snapshot. Used during pre- and post-market
    scans where HTTP doesn't reliably surface extended-hours data.
    """
    raw_quotes: list[dict[str, Any]] = []
    for i in range(0, len(tickers), QUOTE_BATCH_SIZE):
        batch = tickers[i : i + QUOTE_BATCH_SIZE]
        try:
            quotes = await client.live_batch(batch)
        except Exception as e:  # don't let one batch nuke the run
            log.exception("quote batch failed (offset=%d, size=%d): %s", i, len(batch), e)
            continue
        raw_quotes.extend(quotes)

    if ws_collect_seconds > 0 and ws_api_key:
        # Lazy import to keep websockets out of the test-time critical path
        # for code paths that don't enable WS collection.
        from .eodhd_ws import collect_trades_ws
        try:
            ws_results = await collect_trades_ws(
                ws_api_key, tickers, ws_collect_seconds,
            )
            if ws_results:
                _overlay_ws_quotes(raw_quotes, ws_results)
        except Exception as e:
            log.exception("ws collect failed; using HTTP-only snapshot: %s", e)

    hits: list[GapHit] = []
    for q in raw_quotes:
        hit = parse_quote(
            q, max_age_seconds=max_age_seconds, gap_baseline=gap_baseline,
        )
        if hit is not None:
            hits.append(hit)

    hits.sort(key=lambda h: h.gap_pct, reverse=True)
    if hits:
        ext = sum(1 for h in hits if h.source == "extended")
        log.info("scan: %d hits (%d extended, %d regular)", len(hits), ext, len(hits) - ext)
    return hits
