"""
EODHD WebSocket trade collector.

Used to capture extended-hours (pre-market and post-market) trade prints that
the HTTP `/us-quote-delayed` and `/real-time/` endpoints don't reliably surface.

The All-In-One plan (and EOD+Intraday All World Extended) includes the
`/ws/us` trade stream — covers pre-market and post-market hours
(04:00–20:00 ET). Up to 50 symbols per connection by default; this module
chunks the universe into 50-symbol groups and opens one connection per chunk
in parallel.

Usage:

    quotes = await collect_trades_ws(api_key, tickers, duration_s=20)
    # quotes = {"AAPL.US": {"price": 189.42, "ts_ms": 1700000000000,
    #                       "ms_status": "extended hours"}, ...}

Returns the latest trade per symbol seen during the listening window. Symbols
that didn't trade in that window simply don't appear in the result; the caller
falls back to whatever it already had from HTTP.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.exceptions import WebSocketException

log = logging.getLogger(__name__)

WS_URL = "wss://ws.eodhistoricaldata.com/ws/us"
MAX_SYMBOLS_PER_CONN = 50
CONNECT_TIMEOUT_S = 10.0


def _bare_symbol(ticker: str) -> str:
    """EODHD WS expects bare symbols (AAPL, not AAPL.US)."""
    return ticker.split(".")[0].upper()


async def _consume_one_connection(
    api_key: str,
    chunk: list[str],
    duration_s: float,
    results: dict[str, dict[str, Any]],
    lock: asyncio.Lock,
) -> None:
    url = f"{WS_URL}?api_token={api_key}"
    bare_symbols = [_bare_symbol(t) for t in chunk]
    bare_to_full = {_bare_symbol(t): t for t in chunk}

    try:
        async with asyncio.timeout(duration_s + CONNECT_TIMEOUT_S):
            async with websockets.connect(url, open_timeout=CONNECT_TIMEOUT_S) as ws:
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "symbols": ",".join(bare_symbols),
                }))

                loop = asyncio.get_event_loop()
                deadline = loop.time() + duration_s
                while True:
                    timeout = deadline - loop.time()
                    if timeout <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        break

                    try:
                        msg = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(msg, dict):
                        continue

                    sym = msg.get("s")
                    price = msg.get("p")
                    ts_ms = msg.get("t")
                    ms_status = msg.get("ms")
                    if sym is None or price is None or ts_ms is None:
                        # Subscribe ack + status messages also flow through here.
                        continue
                    full = bare_to_full.get(str(sym).upper())
                    if full is None:
                        continue
                    try:
                        price_f = float(price)
                        ts_int = int(ts_ms)
                    except (TypeError, ValueError):
                        continue

                    async with lock:
                        existing = results.get(full)
                        if existing is None or ts_int > existing["ts_ms"]:
                            results[full] = {
                                "price": price_f,
                                "ts_ms": ts_int,
                                "ms_status": ms_status,
                            }
    except (asyncio.TimeoutError, WebSocketException, OSError) as e:
        log.warning(
            "ws chunk failed (%d symbols, first=%s): %s",
            len(chunk), chunk[0] if chunk else "-", e,
        )


async def collect_trades_ws(
    api_key: str,
    tickers: list[str],
    duration_s: float,
    *,
    max_concurrent_connections: int = 12,
) -> dict[str, dict[str, Any]]:
    """
    Open WS connections to /ws/us, subscribe to all `tickers` (chunked at
    MAX_SYMBOLS_PER_CONN per connection), listen for `duration_s` seconds,
    and return per-ticker latest {price, ts_ms, ms_status}.

    Symbols with no trades in the window are simply absent from the result.
    The caller decides what to do with the gaps (typically: keep the HTTP
    snapshot value).

    `max_concurrent_connections` caps in-flight WS connections so we don't
    flood EODHD's gateway when the universe is huge. With chunks of 50,
    a 600-ticker universe = 12 chunks.
    """
    if not tickers or duration_s <= 0:
        return {}
    if not api_key:
        log.warning("ws collector skipped: no api_key")
        return {}

    chunks: list[list[str]] = [
        tickers[i : i + MAX_SYMBOLS_PER_CONN]
        for i in range(0, len(tickers), MAX_SYMBOLS_PER_CONN)
    ]
    results: dict[str, dict[str, Any]] = {}
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(max_concurrent_connections)

    async def runner(chunk: list[str]) -> None:
        async with sem:
            await _consume_one_connection(api_key, chunk, duration_s, results, lock)

    await asyncio.gather(*(runner(c) for c in chunks))
    log.info(
        "ws collect: %d symbols subscribed across %d connections, %d returned trades",
        len(tickers), len(chunks), len(results),
    )
    return results
