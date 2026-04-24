from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id            INTEGER PRIMARY KEY,
    username           TEXT,
    subscribed         INTEGER DEFAULT 1,
    gap_threshold      REAL    DEFAULT 5.0,
    news_enabled       INTEGER DEFAULT 0,
    created_at         TEXT    NOT NULL,
    universe_choice    TEXT    DEFAULT 'all_indices',
    screener_tier      TEXT    DEFAULT 'default',
    premarket_enabled  INTEGER DEFAULT 1,
    postmarket_enabled INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS universe_cache (
    ticker         TEXT PRIMARY KEY,
    market_cap     REAL,
    last_price     REAL,
    avg_daily_vol  REAL,
    refreshed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screener_cache (
    tier           TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    market_cap     REAL,
    last_price     REAL,
    avg_daily_vol  REAL,
    refreshed_at   TEXT NOT NULL,
    PRIMARY KEY (tier, ticker)
);

CREATE TABLE IF NOT EXISTS index_members (
    index_code     TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    company_name   TEXT,
    refreshed_at   TEXT NOT NULL,
    PRIMARY KEY (index_code, ticker)
);

CREATE TABLE IF NOT EXISTS job_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    universe_size  INTEGER,
    hits_count     INTEGER,
    status         TEXT,
    error          TEXT,
    scan_type      TEXT DEFAULT 'premarket'
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

CREATE TABLE IF NOT EXISTS watchlist (
    chat_id        INTEGER NOT NULL,
    ticker         TEXT    NOT NULL,
    company_name   TEXT,
    added_at       TEXT    NOT NULL,
    PRIMARY KEY (chat_id, ticker)
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
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """
        Add columns on existing tables. CREATE TABLE IF NOT EXISTS is a no-op
        when the table already exists, so columns added after v1 need explicit
        ALTER TABLE guarded by PRAGMA.
        """
        # users: added post-v1
        want_user_cols = {
            "universe_choice":    "TEXT DEFAULT 'all_indices'",
            "screener_tier":      "TEXT DEFAULT 'default'",
            "premarket_enabled":  "INTEGER DEFAULT 1",
            "postmarket_enabled": "INTEGER DEFAULT 0",
        }
        await self._ensure_columns("users", want_user_cols)
        # job_runs: added post-v1
        await self._ensure_columns("job_runs", {"scan_type": "TEXT DEFAULT 'premarket'"})

    async def _ensure_columns(self, table: str, wanted: dict[str, str]) -> None:
        async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
            existing = {row["name"] for row in await cur.fetchall()}
        for col, decl in wanted.items():
            if col not in existing:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

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

    async def set_threshold(self, chat_id: int, threshold: float) -> None:
        await self.conn.execute(
            "UPDATE users SET gap_threshold=? WHERE chat_id=?", (threshold, chat_id)
        )
        await self.conn.commit()

    async def set_universe_choice(self, chat_id: int, choice: str) -> None:
        await self.conn.execute(
            "UPDATE users SET universe_choice=? WHERE chat_id=?", (choice, chat_id)
        )
        await self.conn.commit()

    async def set_screener_tier(self, chat_id: int, tier: str) -> None:
        await self.conn.execute(
            "UPDATE users SET screener_tier=? WHERE chat_id=?", (tier, chat_id)
        )
        await self.conn.commit()

    async def set_scan_enabled(self, chat_id: int, scan_type: str, enabled: bool) -> None:
        col = "premarket_enabled" if scan_type == "premarket" else "postmarket_enabled"
        await self.conn.execute(
            f"UPDATE users SET {col}=? WHERE chat_id=?", (1 if enabled else 0, chat_id)
        )
        await self.conn.commit()

    async def get_user(self, chat_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)) as cur:
            return await cur.fetchone()

    async def list_subscribed_users(self, scan_type: str | None = None) -> list[aiosqlite.Row]:
        """
        All subscribed users, optionally filtered to those who opted into a given
        scan_type ('premarket' or 'postmarket').
        """
        if scan_type is None:
            sql = "SELECT * FROM users WHERE subscribed=1"
            params: tuple = ()
        else:
            col = "premarket_enabled" if scan_type == "premarket" else "postmarket_enabled"
            sql = f"SELECT * FROM users WHERE subscribed=1 AND {col}=1"
            params = ()
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    # ---------- screener cache (per tier) ----------

    async def replace_screener_tier(self, tier: str, rows: Iterable[dict[str, Any]]) -> None:
        now = utc_now_iso()
        await self.conn.execute("DELETE FROM screener_cache WHERE tier=?", (tier,))
        await self.conn.executemany(
            """
            INSERT INTO screener_cache
                (tier, ticker, market_cap, last_price, avg_daily_vol, refreshed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (tier, r["ticker"], r.get("market_cap"), r.get("last_price"),
                 r.get("avg_daily_vol"), now)
                for r in rows
            ],
        )
        await self.conn.commit()

    async def get_screener_tickers(self, tier: str) -> list[str]:
        async with self.conn.execute(
            "SELECT ticker FROM screener_cache WHERE tier=? ORDER BY ticker", (tier,)
        ) as cur:
            return [row["ticker"] for row in await cur.fetchall()]

    async def screener_age_seconds(self, tier: str) -> float | None:
        async with self.conn.execute(
            "SELECT MAX(refreshed_at) AS m FROM screener_cache WHERE tier=?", (tier,)
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["m"] is None:
            return None
        ts = datetime.fromisoformat(row["m"])
        return (datetime.now(timezone.utc) - ts).total_seconds()

    # ---------- index members ----------

    async def replace_index_members(
        self, index_code: str, rows: Iterable[dict[str, Any]]
    ) -> None:
        now = utc_now_iso()
        await self.conn.execute(
            "DELETE FROM index_members WHERE index_code=?", (index_code,)
        )
        await self.conn.executemany(
            """
            INSERT INTO index_members (index_code, ticker, company_name, refreshed_at)
            VALUES (?, ?, ?, ?)
            """,
            [(index_code, r["ticker"], r.get("company_name"), now) for r in rows],
        )
        await self.conn.commit()

    async def get_index_tickers(self, index_code: str) -> list[str]:
        async with self.conn.execute(
            "SELECT ticker FROM index_members WHERE index_code=? ORDER BY ticker",
            (index_code,),
        ) as cur:
            return [row["ticker"] for row in await cur.fetchall()]

    async def index_age_seconds(self, index_code: str) -> float | None:
        async with self.conn.execute(
            "SELECT MAX(refreshed_at) AS m FROM index_members WHERE index_code=?",
            (index_code,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["m"] is None:
            return None
        ts = datetime.fromisoformat(row["m"])
        return (datetime.now(timezone.utc) - ts).total_seconds()

    # ---------- job runs ----------

    async def start_job_run(self, scan_type: str = "premarket") -> int:
        cur = await self.conn.execute(
            "INSERT INTO job_runs (started_at, status, scan_type) VALUES (?, 'running', ?)",
            (utc_now_iso(), scan_type),
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

    # ---------- watchlist ----------

    async def add_watch(self, chat_id: int, ticker: str, company_name: str | None) -> bool:
        """Insert or replace. Returns True if the row was new."""
        async with self.conn.execute(
            "SELECT 1 FROM watchlist WHERE chat_id=? AND ticker=?", (chat_id, ticker)
        ) as cur:
            existed = await cur.fetchone() is not None
        await self.conn.execute(
            """
            INSERT INTO watchlist (chat_id, ticker, company_name, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, ticker) DO UPDATE SET
                company_name = excluded.company_name
            """,
            (chat_id, ticker, company_name, utc_now_iso()),
        )
        await self.conn.commit()
        return not existed

    async def remove_watch(self, chat_id: int, ticker: str) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM watchlist WHERE chat_id=? AND ticker=?", (chat_id, ticker)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def list_watch(self, chat_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT ticker, company_name FROM watchlist WHERE chat_id=? ORDER BY ticker",
            (chat_id,),
        ) as cur:
            return list(await cur.fetchall())
