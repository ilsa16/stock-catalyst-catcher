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
async def test_live_batch_unwraps_v2_envelope(client, db):
    """Live v2 returns {meta, data: {ticker: row}, links}; client flattens to a list."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{BASE_URL}/us-quote-delayed").mock(
            return_value=httpx.Response(
                200,
                json={
                    "meta": {"count": 2},
                    "data": {
                        "AAPL.US": {
                            "symbol": "AAPL.US",
                            "lastTradePrice": 110.0,
                            "previousClosePrice": 100.0,
                            "ethPrice": 112.0,
                            "ethTime": 1_770_000_000_000,
                        },
                        "MSFT.US": {
                            "symbol": "MSFT.US",
                            "lastTradePrice": 105.0,
                            "previousClosePrice": 100.0,
                        },
                    },
                    "links": {"next": None},
                },
            )
        )
        rows = await client.live_batch(["AAPL.US", "MSFT.US"])

    assert len(rows) == 2
    by_code = {r["code"]: r for r in rows}
    assert "AAPL.US" in by_code and "MSFT.US" in by_code

    call = route.calls[-1]
    assert call.request.url.params["s"] == "AAPL.US,MSFT.US"
    assert call.request.url.params["api_token"] == "TESTKEY"
    assert await db.credits_used_today() == 2


@pytest.mark.asyncio
async def test_credit_cap_blocks_essential(client, db):
    # Use up the budget
    await db.add_credits(999)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/us-quote-delayed").mock(
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
