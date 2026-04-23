from __future__ import annotations

import aiosqlite
import pytest

from src.db import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    await d.connect()
    yield d
    await d.close()


@pytest.mark.asyncio
async def test_fresh_user_has_new_defaults(db):
    await db.upsert_user(chat_id=1, username=None, default_threshold=5.0)
    row = await db.get_user(1)
    assert row["universe_choice"] == "all_indices"
    assert row["screener_tier"] == "default"
    assert row["premarket_enabled"] == 1
    assert row["postmarket_enabled"] == 0


@pytest.mark.asyncio
async def test_preference_setters(db):
    await db.upsert_user(chat_id=1, username=None, default_threshold=5.0)

    await db.set_threshold(1, 7.5)
    await db.set_universe_choice(1, "sp500")
    await db.set_screener_tier(1, "broad")
    await db.set_scan_enabled(1, "postmarket", True)
    await db.set_scan_enabled(1, "premarket", False)

    row = await db.get_user(1)
    assert row["gap_threshold"] == 7.5
    assert row["universe_choice"] == "sp500"
    assert row["screener_tier"] == "broad"
    assert row["premarket_enabled"] == 0
    assert row["postmarket_enabled"] == 1


@pytest.mark.asyncio
async def test_list_subscribed_users_filters_by_scan_type(db):
    # User 1: default (premarket only). User 2: postmarket on. User 3: both off.
    await db.upsert_user(chat_id=1, username=None, default_threshold=5.0)
    await db.upsert_user(chat_id=2, username=None, default_threshold=5.0)
    await db.set_scan_enabled(2, "postmarket", True)
    await db.upsert_user(chat_id=3, username=None, default_threshold=5.0)
    await db.set_scan_enabled(3, "premarket", False)

    pre = {u["chat_id"] for u in await db.list_subscribed_users(scan_type="premarket")}
    post = {u["chat_id"] for u in await db.list_subscribed_users(scan_type="postmarket")}
    assert pre == {1, 2}
    assert post == {2}


@pytest.mark.asyncio
async def test_screener_cache_roundtrip_per_tier(db):
    await db.replace_screener_tier("default", [
        {"ticker": "A.US", "market_cap": 1, "last_price": 1, "avg_daily_vol": 1},
        {"ticker": "B.US", "market_cap": 2, "last_price": 2, "avg_daily_vol": 2},
    ])
    await db.replace_screener_tier("broad", [
        {"ticker": "C.US", "market_cap": 3, "last_price": 3, "avg_daily_vol": 3},
    ])
    assert await db.get_screener_tickers("default") == ["A.US", "B.US"]
    assert await db.get_screener_tickers("broad") == ["C.US"]
    # Replacing one tier doesn't touch the other.
    await db.replace_screener_tier("default", [
        {"ticker": "Z.US", "market_cap": 9, "last_price": 9, "avg_daily_vol": 9}
    ])
    assert await db.get_screener_tickers("default") == ["Z.US"]
    assert await db.get_screener_tickers("broad") == ["C.US"]


@pytest.mark.asyncio
async def test_index_members_roundtrip(db):
    await db.replace_index_members("sp500", [
        {"ticker": "AAPL.US", "company_name": "Apple"},
        {"ticker": "MSFT.US", "company_name": "Microsoft"},
    ])
    assert await db.get_index_tickers("sp500") == ["AAPL.US", "MSFT.US"]
    assert await db.get_index_tickers("ndx") == []


@pytest.mark.asyncio
async def test_job_runs_track_scan_type(db):
    job_id = await db.start_job_run(scan_type="postmarket")
    await db.finish_job_run(job_id, universe_size=100, hits_count=3, status="ok")
    row = await db.latest_job_run()
    assert row["scan_type"] == "postmarket"
    assert row["hits_count"] == 3


# ---------- migration from a v1-shaped database ----------

@pytest.mark.asyncio
async def test_migration_adds_columns_to_legacy_users_table(tmp_path):
    # Build a v1-era DB by hand: no new columns, no new tables.
    db_path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                subscribed INTEGER DEFAULT 1,
                gap_threshold REAL DEFAULT 5.0,
                news_enabled INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                universe_size INTEGER,
                hits_count INTEGER,
                status TEXT,
                error TEXT
            )
            """
        )
        await conn.execute(
            "INSERT INTO users (chat_id, username, created_at) VALUES (?, ?, ?)",
            (99, "legacy", "2026-01-01T00:00:00+00:00"),
        )
        await conn.commit()

    # Now connect with the new Database class — migration should fill in defaults.
    d = Database(db_path)
    await d.connect()
    try:
        row = await d.get_user(99)
        assert row["universe_choice"] == "all_indices"
        assert row["screener_tier"] == "default"
        assert row["premarket_enabled"] == 1
        assert row["postmarket_enabled"] == 0

        # job_runs migration adds scan_type column.
        async with d.conn.execute("PRAGMA table_info(job_runs)") as cur:
            cols = {r["name"] for r in await cur.fetchall()}
        assert "scan_type" in cols
    finally:
        await d.close()
