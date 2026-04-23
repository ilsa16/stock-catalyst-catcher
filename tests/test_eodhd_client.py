from __future__ import annotations

import httpx
import pytest
import respx

from src.db import Database
from src.eodhd_client import BASE_URL, CreditCapExceeded, EODHDClient


@pytest.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def client(db):
    http = httpx.AsyncClient()
    c = EODHDClient(http, "TESTKEY", db, daily_cap=1000)
    yield c
    await http.aclose()


@pytest.mark.asyncio
async def test_live_batch_passes_remaining_symbols_in_s_param(client, db):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{BASE_URL}/real-time/AAPL.US").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"code": "AAPL.US", "close": 110.0, "previousClose": 100.0, "change_p": 10.0},
                    {"code": "MSFT.US", "close": 105.0, "previousClose": 100.0, "change_p": 5.0},
                ],
            )
        )
        rows = await client.live_batch(["AAPL.US", "MSFT.US"])

    assert len(rows) == 2
    call = route.calls[-1]
    assert call.request.url.params["s"] == "MSFT.US"
    assert call.request.url.params["api_token"] == "TESTKEY"
    assert await db.credits_used_today() == 2


@pytest.mark.asyncio
async def test_credit_cap_blocks_essential(client, db):
    # Use up the budget
    await db.add_credits(999)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/real-time/AAPL.US").mock(
            return_value=httpx.Response(200, json=[{"code": "AAPL.US"}])
        )
        with pytest.raises(CreditCapExceeded):
            await client.live_batch(["AAPL.US", "MSFT.US"])  # cost=2 → would push past 1000


@pytest.mark.asyncio
async def test_credit_cap_blocks_nonessential_at_80pct(client, db):
    # 80% of 1000 = 800. Use 796. A 5-credit news call would push to 801 -> blocked.
    await db.add_credits(796)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/news").mock(
            return_value=httpx.Response(200, json=[{"link": "https://example.com"}])
        )
        result = await client.top_news("AAPL.US")
    assert result is None
    # Counter should not have moved
    assert await db.credits_used_today() == 796


@pytest.mark.asyncio
async def test_search_returns_match_list(client, db):
    with respx.mock() as mock:
        mock.get(f"{BASE_URL}/search/AAPL").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"Code": "AAPL", "Name": "Apple Inc", "Exchange": "NASDAQ"},
                    {"Code": "AAPLW", "Name": "Apple Warrants"},
                ],
            )
        )
        rows = await client.search("AAPL")
    assert len(rows) == 2
    assert rows[0]["Code"] == "AAPL"
    assert await db.credits_used_today() == 1


@pytest.mark.asyncio
async def test_search_gated_as_nonessential(client, db):
    # 80% of 1000 = 800. Use 800 exactly; a 1-credit search pushes to 801 → blocked.
    await db.add_credits(800)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/search/AAPL").mock(
            return_value=httpx.Response(200, json=[{"Code": "AAPL"}])
        )
        result = await client.search("AAPL")
    assert result == []
    assert await db.credits_used_today() == 800


@pytest.mark.asyncio
async def test_screener_returns_data_list(client):
    with respx.mock() as mock:
        mock.get(f"{BASE_URL}/screener").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"code": "AAPL.US", "market_capitalization": 3e12}]},
            )
        )
        rows = await client.screener(market_cap_min=1e9, price_min=10, avg_vol_min=1e5)
    assert rows == [{"code": "AAPL.US", "market_capitalization": 3e12}]
