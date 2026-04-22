from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id        INTEGER PRIMARY KEY,
    username       TEXT,
    subscribed     INTEGER DEFAULT 1,
    gap_threshold  REAL    DEFAULT 5.0,
    news_enabled   INTEGER DEFAULT 0,
    created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_cache (
    ticker         TEXT PRIMARY KEY,
    market_cap     REAL,
    last_price     REAL,
    avg_daily_vol  REAL,
    refreshed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    universe_size  INTEGER,
    hits_count     INTEGER,
    status         TEXT,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS alert_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_run_id     INTEGER NOT NULL,
    chat_id        INTEGER NOT NULL,
    ticker         TEXT    NOT NULL,
    gap_pct        REAL    NOT NULL,
    price          REAL    NOT NULL,
    prior_close    REAL    NOT NULL,
    news_url       TEXT,
    message_id     INTEGER,
    alerted_at_utc TEXT    NOT NULL,
    UNIQUE (job_run_id, chat_id, ticker)
);

CREATE TABLE IF NOT EXISTS api_credits (
    day            TEXT PRIMARY KEY,
    credits_used   INTEGER DEFAULT 0
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Database:
    """Thin async SQLite wrapper. One connection, WAL, awaited from async tasks."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected; call connect() first")
        return self._conn

    # ---------- users ----------

    async def upsert_user(self, chat_id: int, username: str | None, default_threshold: float) -> None:
        await self.conn.execute(
            """
            INSERT INTO users (chat_id, username, subscribed, gap_threshold, news_enabled, created_at)
            VALUES (?, ?, 1, ?, 0, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                username   = excluded.username,
                subscribed = 1
            """,
            (chat_id, username, default_threshold, utc_now_iso()),
        )
        await self.conn.commit()

    async def set_subscribed(self, chat_id: int, subscribed: bool) -> None:
        await self.conn.execute(
            "UPDATE users SET subscribed=? WHERE chat_id=?",
            (1 if subscribed else 0, chat_id),
        )
        await self.conn.commit()

    async def set_news_enabled(self, chat_id: int, enabled: bool) -> None:
        await self.conn.execute(
            "UPDATE users SET news_enabled=? WHERE chat_id=?",
            (1 if enabled else 0, chat_id),
        )
        await self.conn.commit()

    async def get_user(self, chat_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)) as cur:
            return await cur.fetchone()

    async def list_subscribed_users(self) -> list[aiosqlite.Row]:
        async with self.conn.execute("SELECT * FROM users WHERE subscribed=1") as cur:
            return list(await cur.fetchall())

    # ---------- universe ----------

    async def replace_universe(self, rows: Iterable[dict[str, Any]]) -> None:
        now = utc_now_iso()
        await self.conn.execute("DELETE FROM universe_cache")
        await self.conn.executemany(
            """
            INSERT INTO universe_cache (ticker, market_cap, last_price, avg_daily_vol, refreshed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (r["ticker"], r.get("market_cap"), r.get("last_price"), r.get("avg_daily_vol"), now)
                for r in rows
            ],
        )
        await self.conn.commit()

    async def get_universe_tickers(self) -> list[str]:
        async with self.conn.execute("SELECT ticker FROM universe_cache ORDER BY ticker") as cur:
            return [row["ticker"] for row in await cur.fetchall()]

    async def universe_age_seconds(self) -> float | None:
        async with self.conn.execute("SELECT MAX(refreshed_at) AS m FROM universe_cache") as cur:
            row = await cur.fetchone()
        if row is None or row["m"] is None:
            return None
        ts = datetime.fromisoformat(row["m"])
        return (datetime.now(timezone.utc) - ts).total_seconds()

    # ---------- job runs ----------

    async def start_job_run(self) -> int:
        cur = await self.conn.execute(
            "INSERT INTO job_runs (started_at, status) VALUES (?, 'running')",
            (utc_now_iso(),),
        )
        await self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def finish_job_run(
        self,
        job_run_id: int,
        *,
        universe_size: int,
        hits_count: int,
        status: str,
        error: str | None = None,
    ) -> None:
        await self.conn.execute(
            """
            UPDATE job_runs
               SET finished_at=?, universe_size=?, hits_count=?, status=?, error=?
             WHERE id=?
            """,
            (utc_now_iso(), universe_size, hits_count, status, error, job_run_id),
        )
        await self.conn.commit()

    async def latest_job_run(self) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM job_runs ORDER BY id DESC LIMIT 1"
        ) as cur:
            return await cur.fetchone()

    # ---------- alert log ----------

    async def insert_alert(
        self,
        *,
        job_run_id: int,
        chat_id: int,
        ticker: str,
        gap_pct: float,
        price: float,
        prior_close: float,
        news_url: str | None,
        message_id: int | None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO alert_log
                (job_run_id, chat_id, ticker, gap_pct, price, prior_close,
                 news_url, message_id, alerted_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_run_id, chat_id, ticker, gap_pct, price, prior_close,
                news_url, message_id, utc_now_iso(),
            ),
        )
        await self.conn.commit()

    # ---------- credits ----------

    async def add_credits(self, n: int) -> int:
        day = utc_today()
        await self.conn.execute(
            """
            INSERT INTO api_credits (day, credits_used) VALUES (?, ?)
            ON CONFLICT(day) DO UPDATE SET credits_used = credits_used + excluded.credits_used
            """,
            (day, n),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT credits_used FROM api_credits WHERE day=?", (day,)
        ) as cur:
            row = await cur.fetchone()
        return int(row["credits_used"]) if row else 0

    async def credits_used_today(self) -> int:
        async with self.conn.execute(
            "SELECT credits_used FROM api_credits WHERE day=?", (utc_today(),)
        ) as cur:
            row = await cur.fetchone()
        return int(row["credits_used"]) if row else 0
