from __future__ import annotations

import pytest

from src.db import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    await d.connect()
    yield d
    await d.close()


@pytest.mark.asyncio
async def test_add_watch_returns_true_on_new_false_on_existing(db):
    assert await db.add_watch(1, "AAPL.US", "Apple Inc") is True
    assert await db.add_watch(1, "AAPL.US", "Apple Inc") is False


@pytest.mark.asyncio
async def test_add_watch_refreshes_company_name(db):
    await db.add_watch(1, "AAPL.US", "Apple")
    await db.add_watch(1, "AAPL.US", "Apple Inc.")
    rows = await db.list_watch(1)
    assert len(rows) == 1
    assert rows[0]["company_name"] == "Apple Inc."


@pytest.mark.asyncio
async def test_list_watch_scoped_per_chat_and_sorted(db):
    await db.add_watch(1, "MSFT.US", "Microsoft")
    await db.add_watch(1, "AAPL.US", "Apple")
    await db.add_watch(2, "TSLA.US", "Tesla")

    rows1 = await db.list_watch(1)
    assert [r["ticker"] for r in rows1] == ["AAPL.US", "MSFT.US"]

    rows2 = await db.list_watch(2)
    assert [r["ticker"] for r in rows2] == ["TSLA.US"]


@pytest.mark.asyncio
async def test_remove_watch_returns_flag(db):
    await db.add_watch(1, "AAPL.US", "Apple")
    assert await db.remove_watch(1, "AAPL.US") is True
    assert await db.remove_watch(1, "AAPL.US") is False
    assert await db.list_watch(1) == []


@pytest.mark.asyncio
async def test_list_watch_empty_for_new_user(db):
    assert await db.list_watch(999) == []
