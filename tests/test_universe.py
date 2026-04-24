from __future__ import annotations

import httpx
import pytest
import respx

from src.db import Database
from src.eodhd_client import EODHDClient
from src.universe import (
    INDEX_CODES,
    SCREENER_TIERS,
    UNIVERSE_ALL_INDICES,
    UNIVERSE_CUSTOM,
    UNIVERSE_LABELS,
    UNIVERSE_SP500,
    UNIVERSE_WATCHLIST,
    WIKI_URLS,
    ensure_index_members,
    ensure_screener_tier,
    parse_wiki_tickers,
    resolve_union_for_users,
    resolve_user_universe,
)


# ---------- Wikipedia parser ----------

def _wiki_html(rows: list[tuple[str, str]]) -> str:
    tr = "\n".join(
        f"<tr><td><a href='/wiki/{t}'>{t}</a></td><td>{name}</td></tr>"
        for t, name in rows
    )
    return f"""
    <html><body>
    <table class="wikitable sortable">
      <thead><tr><th>Symbol</th><th>Security</th></tr></thead>
      <tbody>
      {tr}
      </tbody>
    </table>
    </body></html>
    """


def test_parse_wiki_tickers_extracts_symbol_column():
    html = _wiki_html([("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet")])
    assert parse_wiki_tickers(html) == ["AAPL", "MSFT", "GOOGL"]


def test_parse_wiki_tickers_detects_ticker_header_variants():
    html = """
    <table class="wikitable">
      <tr><th>Company</th><th>Ticker</th></tr>
      <tr><td>Apple</td><td><a>AAPL</a></td></tr>
      <tr><td>Microsoft</td><td><a>MSFT</a></td></tr>
    </table>
    """
    assert parse_wiki_tickers(html) == ["AAPL", "MSFT"]


def test_parse_wiki_tickers_dedupes_and_preserves_order():
    html = _wiki_html([("AAPL", "A"), ("MSFT", "B"), ("AAPL", "A again")])
    assert parse_wiki_tickers(html) == ["AAPL", "MSFT"]


def test_parse_wiki_tickers_ignores_nested_tables():
    # A nested table inside the first <td> should not pollute the tickers list.
    html = """
    <table class="wikitable">
      <tr><th>Symbol</th></tr>
      <tr><td><a>AAPL</a><table><tr><td><a>JUNK</a></td></tr></table></td></tr>
      <tr><td><a>MSFT</a></td></tr>
    </table>
    """
    assert parse_wiki_tickers(html) == ["AAPL", "MSFT"]


def test_parse_wiki_tickers_empty_when_no_wikitable():
    assert parse_wiki_tickers("<html><body>no tables</body></html>") == []


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


# ---------- index caching ----------

@pytest.mark.asyncio
async def test_ensure_index_members_scrapes_and_caches(db, http):
    # Fake the SP500 wiki page to return 400+ tickers (passes sanity floor).
    rows = [(f"T{i:04d}", f"Co {i}") for i in range(450)]
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WIKI_URLS[UNIVERSE_SP500]).mock(
            return_value=httpx.Response(200, text=_wiki_html(rows))
        )
        tickers = await ensure_index_members(db, http, UNIVERSE_SP500)

    assert len(tickers) == 450
    assert "T0000.US" in tickers
    # Second call uses cache — the mock has assert_all_called, so we close it
    # and ensure no new HTTP is made by using a fresh respx that asserts nothing.
    with respx.mock(assert_all_called=False):
        cached = await ensure_index_members(db, http, UNIVERSE_SP500)
    assert cached == tickers


@pytest.mark.asyncio
async def test_ensure_index_members_rejects_degraded_scrape(db, http):
    # Seed a good cache first.
    good = [{"ticker": f"T{i:04d}.US", "company_name": None} for i in range(450)]
    await db.replace_index_members(UNIVERSE_SP500, good)
    # Force staleness by rewriting refreshed_at to far past.
    await db.conn.execute(
        "UPDATE index_members SET refreshed_at='2000-01-01T00:00:00+00:00' WHERE index_code=?",
        (UNIVERSE_SP500,),
    )
    await db.conn.commit()

    # Degraded scrape returns only 10 rows — should not overwrite.
    degraded_html = _wiki_html([("BAD", "junk")] * 10)
    with respx.mock() as mock:
        mock.get(WIKI_URLS[UNIVERSE_SP500]).mock(
            return_value=httpx.Response(200, text=degraded_html)
        )
        tickers = await ensure_index_members(db, http, UNIVERSE_SP500)

    assert len(tickers) == 450  # cached good list retained


# ---------- screener tier caching ----------

@pytest.mark.asyncio
async def test_ensure_screener_tier_caches_per_tier(db, client):
    # Default tier: return 50 tickers; broad tier: return 80.
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
    # Data got cached under "default", not the bogus key.
    assert tickers == ["X.US"]


# ---------- user-universe resolution ----------

@pytest.mark.asyncio
async def test_resolve_user_universe_watchlist(db, client, http):
    await db.add_watch(1, "AAPL.US", "Apple")
    await db.add_watch(1, "MSFT.US", "Microsoft")
    result = await resolve_user_universe(
        db, client, http,
        choice=UNIVERSE_WATCHLIST, tier="default", chat_id=1,
    )
    assert set(result) == {"AAPL.US", "MSFT.US"}


@pytest.mark.asyncio
async def test_resolve_user_universe_custom_uses_tier(db, client, http):
    # Seed the broad tier directly; resolver should hit cache, no HTTP.
    await db.replace_screener_tier(
        "broad",
        [{"ticker": "BCAST.US", "market_cap": 1e9, "last_price": 5.0, "avg_daily_vol": 1e6}],
    )
    result = await resolve_user_universe(
        db, client, http,
        choice=UNIVERSE_CUSTOM, tier="broad", chat_id=1,
    )
    assert result == ["BCAST.US"]


@pytest.mark.asyncio
async def test_resolve_user_universe_all_indices(db, client, http):
    await db.replace_index_members(
        "sp500", [{"ticker": f"S{i}.US", "company_name": None} for i in range(3)]
    )
    await db.replace_index_members(
        "ndx", [{"ticker": f"N{i}.US", "company_name": None} for i in range(2)]
    )
    await db.replace_index_members(
        "dj30", [{"ticker": "S0.US", "company_name": None}]  # overlaps sp500
    )
    result = await resolve_user_universe(
        db, client, http,
        choice=UNIVERSE_ALL_INDICES, tier="default", chat_id=1,
    )
    assert set(result) == {"S0.US", "S1.US", "S2.US", "N0.US", "N1.US"}
    # Dedup: S0.US appears once, not twice.
    assert len(result) == 5


@pytest.mark.asyncio
async def test_resolve_union_across_users(db, client, http):
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
    union = await resolve_union_for_users(db, client, http, users)
    assert set(union) == {"AAPL.US", "MSFT.US", "TSLA.US"}


# ---------- sanity ----------

def test_universe_labels_cover_all_choices():
    for code in INDEX_CODES:
        assert code in UNIVERSE_LABELS
    assert UNIVERSE_ALL_INDICES in UNIVERSE_LABELS
    assert UNIVERSE_CUSTOM in UNIVERSE_LABELS
    assert UNIVERSE_WATCHLIST in UNIVERSE_LABELS


def test_screener_tiers_cover_ranges():
    keys = set(SCREENER_TIERS.keys())
    assert {"default", "large_cap", "broad", "penny_friendly"} <= keys
    # Penny-friendly admits $1 prices, as per product requirement.
    assert SCREENER_TIERS["penny_friendly"]["price_min"] == 1.0
    # Default is the existing v1 floor.
    assert SCREENER_TIERS["default"]["market_cap_min"] == 1_000_000_000
