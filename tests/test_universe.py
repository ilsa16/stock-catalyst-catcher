from __future__ import annotations

import httpx
import pytest
import respx

from src.db import Database
from src.eodhd_client import BASE_URL, EODHDClient
from src.universe import (
    EODHD_INDEX_SYMBOLS,
    INDEX_CODES,
    SCREENER_TIERS,
    UNIVERSE_ALL_INDICES,
    UNIVERSE_CUSTOM,
    UNIVERSE_LABELS,
    UNIVERSE_SP500,
    UNIVERSE_WATCHLIST,
    ensure_index_members,
    ensure_screener_tier,
    resolve_union_for_users,
    resolve_user_universe,
)


# ---------- shared fixtures ----------

@pytest.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def http():
    c = httpx.AsyncClient()
    yield c
    await c.aclose()


@pytest.fixture
async def client(db, http):
    c = EODHDClient(http, "TESTKEY", db, daily_cap=100000)
    yield c


# ---------- index constituents (EODHD-backed) ----------

def _eodhd_components_payload(tickers: list[tuple[str, str]]) -> dict:
    """Mimic the shape EODHD's /fundamentals/{INDEX}.INDX returns."""
    return {
        "General": {"Code": "GSPC", "Type": "INDEX"},
        "Components": {
            str(i): {"Code": code, "Name": name, "Exchange": "NASDAQ"}
            for i, (code, name) in enumerate(tickers)
        },
    }


@pytest.mark.asyncio
async def test_ensure_index_members_uses_eodhd_fundamentals(db, client):
    rows = [(f"T{i:04d}", f"Co {i}") for i in range(450)]
    sym = EODHD_INDEX_SYMBOLS[UNIVERSE_SP500]
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{BASE_URL}/fundamentals/{sym}.INDX").mock(
            return_value=httpx.Response(200, json=_eodhd_components_payload(rows))
        )
        tickers = await ensure_index_members(db, client, UNIVERSE_SP500)

    assert len(tickers) == 450
    assert "T0000.US" in tickers
    # Second call hits cache, no new HTTP.
    with respx.mock(assert_all_called=False):
        cached = await ensure_index_members(db, client, UNIVERSE_SP500)
    assert cached == tickers


@pytest.mark.asyncio
async def test_ensure_index_members_rejects_degraded_response(db, client):
    """A short response from EODHD shouldn't overwrite a known-good cache."""
    good = [{"ticker": f"T{i:04d}.US", "company_name": None} for i in range(450)]
    await db.replace_index_members(UNIVERSE_SP500, good)
    await db.conn.execute(
        "UPDATE index_members SET refreshed_at='2000-01-01T00:00:00+00:00' WHERE index_code=?",
        (UNIVERSE_SP500,),
    )
    await db.conn.commit()

    sym = EODHD_INDEX_SYMBOLS[UNIVERSE_SP500]
    with respx.mock() as mock:
        mock.get(f"{BASE_URL}/fundamentals/{sym}.INDX").mock(
            return_value=httpx.Response(
                200, json=_eodhd_components_payload([("BAD", "junk")] * 10)
            )
        )
        tickers = await ensure_index_members(db, client, UNIVERSE_SP500)
    assert len(tickers) == 450  # cached good list retained


@pytest.mark.asyncio
async def test_index_constituents_normalizes_class_share_ticker(db, client):
    """BRK.B in EODHD's Components → BRK-B.US (existing normalization rule)."""
    sym = EODHD_INDEX_SYMBOLS[UNIVERSE_SP500]
    rows = [(f"T{i}", "x") for i in range(400)] + [("BRK.B", "Berkshire Hathaway B")]
    with respx.mock() as mock:
        mock.get(f"{BASE_URL}/fundamentals/{sym}.INDX").mock(
            return_value=httpx.Response(200, json=_eodhd_components_payload(rows))
        )
        tickers = await ensure_index_members(db, client, UNIVERSE_SP500)
    assert "BRK-B.US" in tickers


# ---------- screener tier caching ----------

@pytest.mark.asyncio
async def test_ensure_screener_tier_caches_per_tier(db, client):
    with respx.mock() as mock:
        mock.get(url__regex=r".*/screener.*").mock(
            side_effect=[
                httpx.Response(200, json={"data": [
                    {"code": f"D{i:03d}.US"} for i in range(50)
                ]}),
                httpx.Response(200, json={"data": [
                    {"code": f"B{i:03d}.US"} for i in range(80)
                ]}),
            ]
        )
        default_tickers = await ensure_screener_tier(db, client, "default")
        broad_tickers = await ensure_screener_tier(db, client, "broad")

    assert len(default_tickers) == 50
    assert len(broad_tickers) == 80
    assert default_tickers[0].startswith("D")
    assert broad_tickers[0].startswith("B")


@pytest.mark.asyncio
async def test_ensure_screener_tier_falls_back_to_default_for_unknown(db, client):
    with respx.mock() as mock:
        mock.get(url__regex=r".*/screener.*").mock(
            return_value=httpx.Response(200, json={"data": [{"code": "X.US"}]})
        )
        tickers = await ensure_screener_tier(db, client, "bogus_tier")
    assert tickers == ["X.US"]


# ---------- user-universe resolution ----------

@pytest.mark.asyncio
async def test_resolve_user_universe_watchlist(db, client):
    await db.add_watch(1, "AAPL.US", "Apple")
    await db.add_watch(1, "MSFT.US", "Microsoft")
    result = await resolve_user_universe(
        db, client,
        choice=UNIVERSE_WATCHLIST, tier="default", chat_id=1,
    )
    assert set(result) == {"AAPL.US", "MSFT.US"}


@pytest.mark.asyncio
async def test_resolve_user_universe_custom_uses_tier(db, client):
    await db.replace_screener_tier(
        "broad",
        [{"ticker": "BCAST.US", "market_cap": 1e9, "last_price": 5.0, "avg_daily_vol": 1e6}],
    )
    result = await resolve_user_universe(
        db, client,
        choice=UNIVERSE_CUSTOM, tier="broad", chat_id=1,
    )
    assert result == ["BCAST.US"]


@pytest.mark.asyncio
async def test_resolve_user_universe_all_indices(db, client):
    await db.replace_index_members(
        "sp500", [{"ticker": f"S{i}.US", "company_name": None} for i in range(3)]
    )
    await db.replace_index_members(
        "ndx", [{"ticker": f"N{i}.US", "company_name": None} for i in range(2)]
    )
    await db.replace_index_members(
        "dj30", [{"ticker": "S0.US", "company_name": None}]
    )
    result = await resolve_user_universe(
        db, client,
        choice=UNIVERSE_ALL_INDICES, tier="default", chat_id=1,
    )
    assert set(result) == {"S0.US", "S1.US", "S2.US", "N0.US", "N1.US"}
    assert len(result) == 5


@pytest.mark.asyncio
async def test_resolve_union_across_users(db, client):
    await db.replace_index_members(
        "sp500", [{"ticker": "AAPL.US", "company_name": None},
                  {"ticker": "MSFT.US", "company_name": None}]
    )
    await db.add_watch(2, "TSLA.US", "Tesla")

    await db.upsert_user(chat_id=1, username=None, default_threshold=5.0)
    await db.set_universe_choice(1, UNIVERSE_SP500)
    await db.upsert_user(chat_id=2, username=None, default_threshold=5.0)
    await db.set_universe_choice(2, UNIVERSE_WATCHLIST)

    users = await db.list_subscribed_users()
    union = await resolve_union_for_users(db, client, users)
    assert set(union) == {"AAPL.US", "MSFT.US", "TSLA.US"}


# ---------- sanity ----------

def test_universe_labels_cover_all_choices():
    for code in INDEX_CODES:
        assert code in UNIVERSE_LABELS
    assert UNIVERSE_ALL_INDICES in UNIVERSE_LABELS
    assert UNIVERSE_CUSTOM in UNIVERSE_LABELS
    assert UNIVERSE_WATCHLIST in UNIVERSE_LABELS


def test_eodhd_index_symbols_cover_all_index_codes():
    for code in INDEX_CODES:
        assert code in EODHD_INDEX_SYMBOLS


def test_screener_tiers_cover_ranges():
    keys = set(SCREENER_TIERS.keys())
    assert {"default", "large_cap", "broad", "penny_friendly"} <= keys
    assert SCREENER_TIERS["penny_friendly"]["price_min"] == 1.0
    assert SCREENER_TIERS["default"]["market_cap_min"] == 1_000_000_000
