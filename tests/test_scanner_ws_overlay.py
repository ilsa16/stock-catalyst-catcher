"""
Tests for the WebSocket overlay step in scan_universe — the bit that takes a
real-time WS trade dict and merges it onto the HTTP snapshot so parse_quote
sees the freshest available print.

We don't test the WS protocol layer end-to-end (that needs a live network
and is best smoke-tested on the droplet). The overlay function is pure
in/out and covers the integration risk.
"""

from __future__ import annotations

import pytest
import respx
import httpx

from src.eodhd_client import BASE_URL, EODHDClient
from src.scanner import _overlay_ws_quotes, scan_universe


def test_overlay_replaces_when_ws_is_newer():
    raw = [{
        "code": "TECH.US",
        "previousClosePrice": 51.95,
        "lastTradePrice": 55.32,
        "lastTradeTime": 1_777_579_222_606,  # April 30 16:00:22 EDT
    }]
    ws = {
        "TECH.US": {
            "price": 54.05,
            "ts_ms": 1_777_588_260_000,  # April 30 18:31:00 EDT, newer
            "ms_status": "extended hours",
        }
    }
    _overlay_ws_quotes(raw, ws)
    assert raw[0]["lastTradePrice"] == 54.05
    assert raw[0]["lastTradeTime"] == 1_777_588_260_000


def test_overlay_keeps_http_when_ws_is_older():
    raw = [{
        "code": "TECH.US",
        "previousClosePrice": 51.95,
        "lastTradePrice": 55.32,
        "lastTradeTime": 1_777_579_222_606,
    }]
    ws = {
        "TECH.US": {
            "price": 50.00,
            "ts_ms": 1_777_500_000_000,  # older than HTTP
            "ms_status": "extended hours",
        }
    }
    _overlay_ws_quotes(raw, ws)
    assert raw[0]["lastTradePrice"] == 55.32  # unchanged


def test_overlay_leaves_ungentioned_tickers_alone():
    raw = [
        {"code": "AAPL.US", "lastTradePrice": 100, "lastTradeTime": 1},
        {"code": "MSFT.US", "lastTradePrice": 200, "lastTradeTime": 1},
    ]
    ws = {"AAPL.US": {"price": 105.0, "ts_ms": 99, "ms_status": "extended hours"}}
    _overlay_ws_quotes(raw, ws)
    assert raw[0]["lastTradePrice"] == 105.0  # AAPL got overlaid
    assert raw[1]["lastTradePrice"] == 200    # MSFT untouched


def test_overlay_handles_missing_or_garbage_http_timestamp():
    raw = [
        {"code": "X.US", "lastTradePrice": 10.0},  # no timestamp
        {"code": "Y.US", "lastTradePrice": 20.0, "lastTradeTime": "garbage"},
    ]
    ws = {
        "X.US": {"price": 11.0, "ts_ms": 1_000, "ms_status": "open"},
        "Y.US": {"price": 22.0, "ts_ms": 1_000, "ms_status": "open"},
    }
    _overlay_ws_quotes(raw, ws)
    assert raw[0]["lastTradePrice"] == 11.0
    assert raw[1]["lastTradePrice"] == 22.0


@pytest.fixture
async def db(tmp_path):
    from src.db import Database
    d = Database(str(tmp_path / "t.db"))
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def http_client():
    c = httpx.AsyncClient()
    yield c
    await c.aclose()


@pytest.fixture
async def client(db, http_client):
    yield EODHDClient(http_client, "TESTKEY", db, daily_cap=100000)


@pytest.mark.asyncio
async def test_scan_universe_skips_ws_when_seconds_zero(client, monkeypatch):
    """ws_collect_seconds=0 must not invoke the WS collector at all."""
    called = {"n": 0}

    async def fake_collect(*a, **kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr("src.eodhd_ws.collect_trades_ws", fake_collect)
    with respx.mock() as mock:
        mock.get(f"{BASE_URL}/us-quote-delayed").mock(
            return_value=httpx.Response(200, json={"data": {"AAPL.US": {
                "previousClosePrice": 100.0, "lastTradePrice": 110.0,
                "lastTradeTime": 1_700_000_000_000,
            }}})
        )
        await scan_universe(client, ["AAPL.US"], ws_collect_seconds=0)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_scan_universe_invokes_ws_when_enabled(client, monkeypatch):
    """ws_collect_seconds>0 with an api key must invoke the WS collector once
    with the universe."""
    captured = {}

    async def fake_collect(api_key, tickers, duration_s, **kw):
        captured["api_key"] = api_key
        captured["tickers"] = list(tickers)
        captured["duration_s"] = duration_s
        return {}  # no WS overlay

    monkeypatch.setattr("src.eodhd_ws.collect_trades_ws", fake_collect)
    with respx.mock() as mock:
        mock.get(f"{BASE_URL}/us-quote-delayed").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        await scan_universe(
            client, ["AAPL.US", "MSFT.US"],
            ws_collect_seconds=15,
            ws_api_key="WS_TOKEN",
        )

    assert captured["api_key"] == "WS_TOKEN"
    assert set(captured["tickers"]) == {"AAPL.US", "MSFT.US"}
    assert captured["duration_s"] == 15
