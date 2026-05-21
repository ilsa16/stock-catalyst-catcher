"""
Smoke tests that exercise the bot's wiring end-to-end without talking to
Telegram. Catches the kind of breakage that silently leaves a deployed bot
running but unresponsive — import errors, handler registration mistakes,
MarkdownV2 strings that don't parse, daily_scan blowing up before reply.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from config import Settings
from src.db import Database
from src.eodhd_client import EODHDClient
from src.formatter import escape_md_v2, render_digest, render_status
from src.jobs import daily_scan
from src.scheduler import POSTMARKET_JOB_ID, PREMARKET_JOB_ID, build_scheduler
from src.telegram_bot import (
    HELP_TEXT,
    _frequency_text,
    _screener_text,
    _threshold_text,
    _universe_text,
    _welcome_text,
)


@pytest.fixture
async def db(tmp_path):
    d = Database(str(tmp_path / "smoke.db"))
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
    return EODHDClient(http, "TESTKEY", db, daily_cap=100000)


@pytest.fixture
def settings():
    return Settings(
        eodhd_api_key="x", telegram_bot_token="123:abc",
        scan_hour=11, scan_minute=30,
        post_scan_hour=23, post_scan_minute=30,
        scan_timezone="Europe/Nicosia",
    )


# ---------- panel-text MarkdownV2 sanity ----------

# Telegram's MarkdownV2 reserves these. Every literal occurrence must be
# preceded by a backslash, except where it's part of intentional formatting
# (paired *...* for bold, _..._ for italic, [text](url) for links).
_MD_V2_RESERVED = set("_*[]()~`>#+-=|{}.!\\")


def _check_md_v2_balanced(text: str) -> None:
    """
    A coarse sanity check: every reserved char must either be escaped (preceded
    by a backslash) OR be part of a paired delimiter pair. Flags only the
    obvious "definitely-broken" cases like an unescaped period or unbalanced
    bracket — not every nuance of MD V2 — but catches the kind of breakage
    that makes Telegram silently drop a message.
    """
    # Strip well-formed inline links [text](url): no unescaped chars inside url.
    import re
    stripped = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)

    in_bold = False
    in_italic = False
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "\\":
            i += 2  # skip the escaped char
            continue
        if ch == "*":
            in_bold = not in_bold
            i += 1
            continue
        if ch == "_":
            in_italic = not in_italic
            i += 1
            continue
        if ch in _MD_V2_RESERVED:
            raise AssertionError(
                f"Unescaped reserved char {ch!r} at position {i} in {stripped!r}"
            )
        i += 1

    if in_bold:
        raise AssertionError(f"Unbalanced * in {text!r}")
    if in_italic:
        raise AssertionError(f"Unbalanced _ in {text!r}")


def test_help_text_is_valid_md_v2():
    _check_md_v2_balanced(HELP_TEXT)


def test_universe_panel_text_is_valid_md_v2():
    for choice in ("all_indices", "sp500", "ndx", "dj30", "custom", "watchlist"):
        _check_md_v2_balanced(_universe_text(choice))


def test_screener_panel_text_is_valid_md_v2():
    for tier in ("mega_cap", "default", "large_cap", "broad", "penny_friendly"):
        _check_md_v2_balanced(_screener_text(tier))


def test_threshold_panel_text_is_valid_md_v2():
    for thr in (3.0, 5.0, 7.0, 10.0, 15.0):
        _check_md_v2_balanced(_threshold_text(thr))


def test_frequency_panel_text_is_valid_md_v2():
    for pre in (True, False):
        for post in (True, False):
            _check_md_v2_balanced(_frequency_text(pre, post))


def test_welcome_text_is_valid_md_v2_for_every_universe_choice():
    for choice in ("all_indices", "sp500", "ndx", "dj30", "custom", "watchlist"):
        for pre in (True, False):
            for post in (True, False):
                txt = _welcome_text(
                    universe_choice=choice,
                    gap_threshold=5.0,
                    premarket_enabled=pre,
                    postmarket_enabled=post,
                )
                _check_md_v2_balanced(txt)


def test_render_status_threshold_uses_unicode_geq():
    """
    Regression: /status used to emit `\\>= 5.0%` which contains an unescaped
    `=` (a MarkdownV2-reserved char). Telegram returned 400 and the message
    silently never reached the user. Now uses Unicode `≥`.
    """
    text = render_status(
        subscribed=True, threshold=5.0, news_enabled=False,
        universe_label="X", tier_label=None,
        premarket_enabled=False, postmarket_enabled=False,
        next_premarket_local=None, next_postmarket_local=None,
        last_run=None,
    )
    assert "\\>=" not in text
    assert "≥" in text


def test_render_status_is_valid_md_v2():
    text = render_status(
        subscribed=True, threshold=5.0, news_enabled=False,
        universe_label="All indices (S&P 500 + NASDAQ-100 + Dow 30)",
        tier_label=None,
        premarket_enabled=True, postmarket_enabled=False,
        next_premarket_local=datetime(2026, 4, 28, 11, 30, tzinfo=ZoneInfo("Europe/Nicosia")),
        next_postmarket_local=None,
        last_run={"status": "ok", "hits_count": 4, "universe_size": 530,
                  "scan_type": "premarket", "finished_at": "2026-04-27T09:00:00+00:00"},
    )
    _check_md_v2_balanced(text)


def test_render_digest_no_hits_is_valid_md_v2():
    when = datetime(2026, 4, 28, 11, 30, tzinfo=ZoneInfo("Europe/Nicosia"))
    chunks = render_digest([], threshold=5.0, universe_size=530, scan_time_local=when)
    for chunk in chunks:
        _check_md_v2_balanced(chunk)


# ---------- bot wiring ----------

def test_build_telegram_app_registers_all_commands(settings, db, client):
    """
    Imports + handler-registration smoke test. Catches a TypeError in the
    factory or a missing command before deploying.
    """
    from src.telegram_bot import build_telegram_app

    app = build_telegram_app(settings, db, client, scheduler_ref={"sched": None})
    # python-telegram-bot stores handlers in app.handlers[group_id] -> list
    handler_names = []
    for group in app.handlers.values():
        for h in group:
            cmds = getattr(h, "commands", None)
            if cmds:
                handler_names.extend(cmds)

    expected = {
        "start", "stop", "help", "status", "run_now",
        "newson", "newsoff", "watch", "unwatch", "portfolio",
        "universe", "screener", "threshold", "frequency",
    }
    missing = expected - set(handler_names)
    assert not missing, f"missing handlers: {missing}"


def test_build_scheduler_registers_both_jobs(settings, db, client):
    bot = MagicMock()
    sched = build_scheduler(db, client, bot, settings)
    # Don't shutdown — AsyncIOScheduler.shutdown needs a running loop, and we
    # never started one. Just verify the jobs are registered.
    ids = {j.id for j in sched.get_jobs()}
    assert PREMARKET_JOB_ID in ids
    assert POSTMARKET_JOB_ID in ids


# ---------- daily_scan end-to-end ----------

@pytest.mark.asyncio
async def test_daily_scan_no_subscribed_users_returns_quickly(db, client, settings):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    result = await daily_scan(db, client, bot, settings, scan_type="premarket")
    assert result["status"] == "ok"
    assert result["universe_size"] == 0
    assert result["hits_count"] == 0
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_daily_scan_only_chat_id_sends_one_digest(db, client, settings):
    """
    Full happy path: one user on watchlist universe, /run_now-style call.
    Mocks the EODHD live_batch to return an above-threshold quote; verifies
    a Telegram message goes out with the expected content.
    """
    await db.upsert_user(chat_id=42, username="me", default_threshold=5.0)
    await db.set_universe_choice(42, "watchlist")
    await db.add_watch(42, "AAPL.US", "Apple Inc")

    # Mock the EODHD client's live_batch to avoid network.
    quote = {"code": "AAPL.US", "close": 110.0, "previousClose": 100.0, "change_p": 10.0}
    client.live_batch = AsyncMock(return_value=[quote])  # type: ignore[method-assign]

    sent: list[dict] = []

    async def fake_send_message(chat_id, text, **kw):
        sent.append({"chat_id": chat_id, "text": text})
        m = MagicMock()
        m.message_id = len(sent)
        return m

    bot = MagicMock()
    bot.send_message = fake_send_message

    result = await daily_scan(
        db, client, bot, settings,
        scan_type="premarket", only_chat_id=42,
    )
    assert result["status"] == "ok", result.get("error")
    assert result["universe_size"] == 1
    assert result["hits_count"] == 1
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 42
    assert "AAPL" in sent[0]["text"]
    assert "10\\.00%" in sent[0]["text"] or "+10\\.00%" in sent[0]["text"]


@pytest.mark.asyncio
async def test_tier_filter_drops_under_price_floor_on_index_universe(
    db, client, settings,
):
    """
    Reproduces the May-20 bug: user on 'all_indices' with screener_tier=
    'large_cap' was seeing $0.68 names because the tier filter only fired
    when universe=custom. After the fix the per-user filter applies to
    every universe except watchlist, so the sub-$10 name is dropped.
    """
    await db.upsert_user(chat_id=99, username=None, default_threshold=3.0)
    await db.set_universe_choice(99, "sp500")  # any index universe will do
    await db.set_screener_tier(99, "large_cap")  # price_min=10, mcap_min=2B
    # Seed the index_members so resolve_user_universe returns these two.
    await db.replace_index_members("sp500", [
        {"ticker": "BIG.US", "company_name": "Big Co"},
        {"ticker": "TINY.US", "company_name": "Tiny Co"},
    ])

    quotes = [
        {  # passes: price $50, MCap $50B, ADV 2M, gap +8%
            "code": "BIG.US",
            "previousClosePrice": 46.30,
            "lastTradePrice": 50.0,
            "lastTradeTime": int(__import__("time").time() * 1000),
            "marketCap": 50_000_000_000,
            "averageVolume": 2_000_000,
        },
        {  # tier rejects: $0.68 < $10 price floor (and tiny MCap)
            "code": "TINY.US",
            "previousClosePrice": 0.58,
            "lastTradePrice": 0.68,
            "lastTradeTime": int(__import__("time").time() * 1000),
            "marketCap": 50_000_000,
            "averageVolume": 1_500_000,
        },
    ]
    client.live_batch = AsyncMock(return_value=quotes)  # type: ignore[method-assign]

    sent: list[dict] = []

    async def fake_send_message(chat_id, text, **kw):
        sent.append({"chat_id": chat_id, "text": text})
        m = MagicMock()
        m.message_id = len(sent)
        return m

    bot = MagicMock()
    bot.send_message = fake_send_message

    result = await daily_scan(
        db, client, bot, settings,
        scan_type="premarket", only_chat_id=99,
    )
    assert result["status"] == "ok", result.get("error")
    # Both are above ABSOLUTE_GAP_FLOOR=5%, but TINY should be tier-filtered.
    assert result["hits_count"] == 2  # scan_universe returns both
    # The user's digest should only mention BIG.
    body = sent[0]["text"]
    assert "BIG" in body
    assert "TINY" not in body


@pytest.mark.asyncio
async def test_tier_filter_skipped_for_watchlist(db, client, settings):
    """Watchlist is explicit user choice — tier filter doesn't apply, so
    sub-$10 names the user added stay visible."""
    await db.upsert_user(chat_id=100, username=None, default_threshold=3.0)
    await db.set_universe_choice(100, "watchlist")
    await db.set_screener_tier(100, "large_cap")  # would filter $0.68 if applied
    await db.add_watch(100, "TINY.US", "Tiny Co")

    quote = {
        "code": "TINY.US",
        "previousClosePrice": 0.58,
        "lastTradePrice": 0.68,
        "lastTradeTime": int(__import__("time").time() * 1000),
        "marketCap": 50_000_000,
        "averageVolume": 1_500_000,
    }
    client.live_batch = AsyncMock(return_value=[quote])  # type: ignore[method-assign]

    sent: list[dict] = []

    async def fake_send_message(chat_id, text, **kw):
        sent.append({"chat_id": chat_id, "text": text})
        m = MagicMock()
        m.message_id = len(sent)
        return m

    bot = MagicMock()
    bot.send_message = fake_send_message

    await daily_scan(
        db, client, bot, settings,
        scan_type="premarket", only_chat_id=100,
    )
    assert len(sent) == 1
    assert "TINY" in sent[0]["text"]
