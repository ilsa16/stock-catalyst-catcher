from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import Settings

from .db import Database
from .eodhd_client import EODHDClient
from .formatter import escape_md_v2, render_portfolio, render_status
from .jobs import daily_scan
from .scheduler import POSTMARKET_JOB_ID, PREMARKET_JOB_ID
from .universe import (
    SCREENER_TIERS,
    UNIVERSE_CUSTOM,
    UNIVERSE_LABELS,
)

log = logging.getLogger(__name__)

RUN_NOW_COOLDOWN_SECONDS = 5 * 60

THRESHOLD_CHOICES: list[float] = [3.0, 5.0, 7.0, 10.0, 15.0]

HELP_TEXT = (
    "*Catalyst Catcher*\n"
    "/start — subscribe and see defaults\n"
    "/stop — pause alerts\n"
    "/status — show all your settings\n"
    "/universe — pick which stocks to scan\n"
    "/screener — tune the custom screener filters\n"
    "/threshold — set the gap % floor\n"
    "/frequency — pick pre\\-market / post\\-market\n"
    "/newson /newsoff — attach a news link per hit\n"
    "/watch /unwatch /portfolio — manage your watchlist\n"
    "/run\\_now — trigger a scan now \\(rate\\-limited\\)\n"
    "/help — this help"
)


def _normalize_ticker(raw: str) -> str:
    raw = raw.strip().upper()
    if "." not in raw:
        raw = f"{raw}.US"
    return raw


async def _resolve_company_name(client: EODHDClient, ticker: str) -> str | None:
    bare = ticker.split(".")[0]
    matches = await client.search(bare)
    if not matches:
        return None
    for m in matches:
        code = (m.get("Code") or m.get("code") or "").upper()
        if code == bare:
            return m.get("Name") or m.get("name")
    first = matches[0]
    return first.get("Name") or first.get("name")


# ---------- keyboard builders ----------

def _universe_keyboard(current: str) -> InlineKeyboardMarkup:
    def b(key: str, label: str) -> InlineKeyboardButton:
        marker = "✅ " if key == current else ""
        return InlineKeyboardButton(f"{marker}{label}", callback_data=f"univ:{key}")

    rows = [
        [b("all_indices", "All indices")],
        [b("sp500", "S&P 500"), b("ndx", "NASDAQ-100")],
        [b("dj30", "Dow 30"), b("custom", "Custom screener")],
        [b("watchlist", "My watchlist")],
    ]
    return InlineKeyboardMarkup(rows)


def _screener_keyboard(current: str) -> InlineKeyboardMarkup:
    def b(key: str) -> InlineKeyboardButton:
        marker = "✅ " if key == current else ""
        label = SCREENER_TIERS[key]["label"]
        return InlineKeyboardButton(f"{marker}{label}", callback_data=f"tier:{key}")

    rows = [[b(k)] for k in ("default", "large_cap", "broad", "penny_friendly")]
    return InlineKeyboardMarkup(rows)


def _threshold_keyboard(current: float) -> InlineKeyboardMarkup:
    def b(val: float) -> InlineKeyboardButton:
        marker = "✅ " if abs(val - current) < 0.01 else ""
        return InlineKeyboardButton(f"{marker}{val:g}%", callback_data=f"thr:{val}")

    return InlineKeyboardMarkup([[b(v) for v in THRESHOLD_CHOICES]])


def _frequency_keyboard(pre_on: bool, post_on: bool) -> InlineKeyboardMarkup:
    pre_label = ("✅" if pre_on else "⬜") + " Pre-market (11:30 Nicosia / 04:30 ET)"
    post_label = ("✅" if post_on else "⬜") + " Post-market (23:30 Nicosia / 16:30 ET)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(pre_label, callback_data="freq:premarket")],
        [InlineKeyboardButton(post_label, callback_data="freq:postmarket")],
    ])


# ---------- panel renderers ----------

def _universe_text(current: str) -> str:
    return (
        "*Universe*\n"
        f"Current: {escape_md_v2(UNIVERSE_LABELS[current])}\n"
        "Tap an option to change:"
    )


def _screener_text(current: str) -> str:
    return (
        "*Custom screener tier*\n"
        f"Current: {escape_md_v2(SCREENER_TIERS[current]['label'])}\n"
        "_Used when your universe is set to Custom screener\\._\n"
        "Tap an option to change:"
    )


def _threshold_text(current: float) -> str:
    return (
        "*Gap threshold*\n"
        f"Current: ≥ {escape_md_v2(f'{current:.1f}%')}\n"
        "Tap an option to change:"
    )


def _frequency_text(pre_on: bool, post_on: bool) -> str:
    return (
        "*Scan frequency*\n"
        f"Pre\\-market: {'on' if pre_on else 'off'}\n"
        f"Post\\-market: {'on' if post_on else 'off'}\n"
        "Tap a row to toggle:"
    )


def _welcome_text(
    *, universe_choice: str, gap_threshold: float,
    premarket_enabled: bool, postmarket_enabled: bool,
) -> str:
    universe_label = escape_md_v2(UNIVERSE_LABELS[universe_choice])
    threshold_str = escape_md_v2(f"{gap_threshold:.1f}%")
    pre_part = "pre\\-market" if premarket_enabled else "off"
    post_part = " \\+ post\\-market" if postmarket_enabled else ""
    return (
        "*Welcome to Catalyst Catcher*\n"
        "You're subscribed with defaults:\n"
        f"• Universe: {universe_label}\n"
        f"• Threshold: ≥ {threshold_str}\n"
        f"• Schedule: {pre_part}{post_part}\n\n"
        "Customize any time:\n"
        "/universe /screener /threshold /frequency\n"
        "Manage a personal list with /watch and /portfolio\\."
    )


def build_telegram_app(
    settings: Settings,
    db: Database,
    client: EODHDClient,
    http: httpx.AsyncClient,
    scheduler_ref: dict,
) -> Application:
    """
    `scheduler_ref` is a one-key dict {"sched": AsyncIOScheduler|None} populated by
    main() after scheduler.start(). The bot is built before the scheduler so the
    bot can hand its `Bot` instance to the scheduled jobs.
    """
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    last_run_now: dict[int, float] = {}
    run_now_lock = asyncio.Lock()

    async def _ensure_user(chat_id: int, username: str | None) -> None:
        await db.upsert_user(
            chat_id=chat_id,
            username=username,
            default_threshold=settings.default_gap_threshold,
        )

    async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if msg is None or chat is None:
            return
        await _ensure_user(chat.id, user.username if user else None)
        row = await db.get_user(chat.id)
        assert row is not None

        text = _welcome_text(
            universe_choice=row["universe_choice"],
            gap_threshold=float(row["gap_threshold"]),
            premarket_enabled=bool(row["premarket_enabled"]),
            postmarket_enabled=bool(row["postmarket_enabled"]),
        )
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_stop(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return
        await db.set_subscribed(chat.id, False)
        await msg.reply_text("Paused. /start to resume.")

    async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if msg is None:
            return
        await msg.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_news_on(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return
        await db.set_news_enabled(chat.id, True)
        await msg.reply_text("News lookups enabled.")

    async def cmd_news_off(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return
        await db.set_news_enabled(chat.id, False)
        await msg.reply_text("News lookups disabled.")

    async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return
        user_row = await db.get_user(chat.id)
        if user_row is None:
            await msg.reply_text("Not subscribed. /start to subscribe.")
            return

        tz = ZoneInfo(settings.scan_timezone)
        sched: AsyncIOScheduler | None = scheduler_ref.get("sched")
        next_pre: datetime | None = None
        next_post: datetime | None = None
        if sched is not None:
            job = sched.get_job(PREMARKET_JOB_ID)
            if job is not None and job.next_run_time is not None:
                next_pre = job.next_run_time.astimezone(tz)
            job = sched.get_job(POSTMARKET_JOB_ID)
            if job is not None and job.next_run_time is not None:
                next_post = job.next_run_time.astimezone(tz)

        last = await db.latest_job_run()
        choice = user_row["universe_choice"]
        tier_label = (
            SCREENER_TIERS[user_row["screener_tier"]]["label"]
            if choice == UNIVERSE_CUSTOM else None
        )
        text = render_status(
            subscribed=bool(user_row["subscribed"]),
            threshold=float(user_row["gap_threshold"]),
            news_enabled=bool(user_row["news_enabled"]),
            universe_label=UNIVERSE_LABELS[choice],
            tier_label=tier_label,
            premarket_enabled=bool(user_row["premarket_enabled"]),
            postmarket_enabled=bool(user_row["postmarket_enabled"]),
            next_premarket_local=next_pre,
            next_postmarket_local=next_post,
            last_run=dict(last) if last else None,
        )
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_universe(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if msg is None or chat is None:
            return
        await _ensure_user(chat.id, user.username if user else None)
        row = await db.get_user(chat.id)
        assert row is not None
        await msg.reply_text(
            _universe_text(row["universe_choice"]),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_universe_keyboard(row["universe_choice"]),
        )

    async def cmd_screener(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if msg is None or chat is None:
            return
        await _ensure_user(chat.id, user.username if user else None)
        row = await db.get_user(chat.id)
        assert row is not None
        await msg.reply_text(
            _screener_text(row["screener_tier"]),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_screener_keyboard(row["screener_tier"]),
        )

    async def cmd_threshold(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if msg is None or chat is None:
            return
        await _ensure_user(chat.id, user.username if user else None)
        row = await db.get_user(chat.id)
        assert row is not None
        await msg.reply_text(
            _threshold_text(float(row["gap_threshold"])),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_threshold_keyboard(float(row["gap_threshold"])),
        )

    async def cmd_frequency(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if msg is None or chat is None:
            return
        await _ensure_user(chat.id, user.username if user else None)
        row = await db.get_user(chat.id)
        assert row is not None
        pre_on = bool(row["premarket_enabled"])
        post_on = bool(row["postmarket_enabled"])
        await msg.reply_text(
            _frequency_text(pre_on, post_on),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_frequency_keyboard(pre_on, post_on),
        )

    async def _safe_edit(q, text: str, reply_markup) -> None:
        """Edit-in-place, swallowing the 'message is not modified' 400 that
        fires when the user re-taps an already-selected option."""
        try:
            await q.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup,
            )
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            raise

    async def on_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if q is None or q.data is None or q.message is None:
            return
        chat_id = q.message.chat.id
        data = q.data
        try:
            prefix, _, value = data.partition(":")
            if prefix == "univ":
                if value not in UNIVERSE_LABELS:
                    await q.answer("Unknown option")
                    return
                await db.set_universe_choice(chat_id, value)
                await q.answer(f"Universe: {UNIVERSE_LABELS[value]}")
                await _safe_edit(q, _universe_text(value), _universe_keyboard(value))
            elif prefix == "tier":
                if value not in SCREENER_TIERS:
                    await q.answer("Unknown tier")
                    return
                await db.set_screener_tier(chat_id, value)
                await q.answer(f"Tier: {value.replace('_', ' ')}")
                await _safe_edit(q, _screener_text(value), _screener_keyboard(value))
            elif prefix == "thr":
                try:
                    threshold = float(value)
                except ValueError:
                    await q.answer("Bad threshold")
                    return
                await db.set_threshold(chat_id, threshold)
                await q.answer(f"Threshold: {threshold:g}%")
                await _safe_edit(q, _threshold_text(threshold), _threshold_keyboard(threshold))
            elif prefix == "freq":
                if value not in ("premarket", "postmarket"):
                    await q.answer("Unknown slot")
                    return
                row = await db.get_user(chat_id)
                if row is None:
                    await q.answer("Not subscribed")
                    return
                col = "premarket_enabled" if value == "premarket" else "postmarket_enabled"
                new_state = not bool(row[col])
                await db.set_scan_enabled(chat_id, value, new_state)
                pre_on = new_state if value == "premarket" else bool(row["premarket_enabled"])
                post_on = new_state if value == "postmarket" else bool(row["postmarket_enabled"])
                await q.answer(f"{value}: {'on' if new_state else 'off'}")
                await _safe_edit(q, _frequency_text(pre_on, post_on), _frequency_keyboard(pre_on, post_on))
            else:
                await q.answer()
        except Exception as e:
            log.exception("callback handler error: %s", e)
            try:
                await q.answer("Something went wrong — try again.")
            except Exception:
                pass

    async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return
        args = ctx.args or []
        if not args:
            await msg.reply_text("Usage: /watch TICKER [TICKER ...]")
            return

        added, updated, failed = [], [], []
        for raw in args:
            ticker = _normalize_ticker(raw)
            try:
                name = await _resolve_company_name(client, ticker)
            except Exception as e:
                log.exception("search failed for %s: %s", ticker, e)
                failed.append(ticker.split(".")[0])
                continue
            if name is None:
                failed.append(ticker.split(".")[0])
                continue
            was_new = await db.add_watch(chat.id, ticker, name)
            (added if was_new else updated).append(f"{ticker.split('.')[0]} ({name})")

        lines = []
        if added:
            lines.append("Added: " + ", ".join(added))
        if updated:
            lines.append("Already tracked: " + ", ".join(updated))
        if failed:
            lines.append("Couldn't resolve: " + ", ".join(failed))
        await msg.reply_text("\n".join(lines) or "Nothing changed.")

    async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return
        args = ctx.args or []
        if not args:
            await msg.reply_text("Usage: /unwatch TICKER [TICKER ...]")
            return

        removed, missing = [], []
        for raw in args:
            ticker = _normalize_ticker(raw)
            if await db.remove_watch(chat.id, ticker):
                removed.append(ticker.split(".")[0])
            else:
                missing.append(ticker.split(".")[0])

        lines = []
        if removed:
            lines.append("Removed: " + ", ".join(removed))
        if missing:
            lines.append("Not on watchlist: " + ", ".join(missing))
        await msg.reply_text("\n".join(lines) or "Nothing changed.")

    async def cmd_portfolio(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return

        rows = await db.list_watch(chat.id)
        if not rows:
            for chunk in render_portfolio([]):
                await msg.reply_text(chunk, parse_mode=ParseMode.MARKDOWN_V2)
            return

        tickers = [r["ticker"] for r in rows]
        try:
            quotes = await client.live_batch(tickers)
        except Exception as e:
            log.exception("portfolio quote fetch failed: %s", e)
            quotes = []

        price_by_ticker: dict[str, float] = {}
        for q in quotes:
            code = (q.get("code") or q.get("Code") or "").upper()
            try:
                price = float(q.get("close")) if q.get("close") not in (None, "NA") else None
            except (TypeError, ValueError):
                price = None
            if code and price is not None:
                price_by_ticker[code] = price

        payload = [
            {
                "ticker": r["ticker"],
                "company_name": r["company_name"],
                "price": price_by_ticker.get(r["ticker"]),
            }
            for r in rows
        ]
        for chunk in render_portfolio(payload):
            await msg.reply_text(chunk, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_run_now(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return

        now = time.monotonic()
        prev = last_run_now.get(chat.id, 0.0)
        wait = RUN_NOW_COOLDOWN_SECONDS - (now - prev)
        if wait > 0:
            await msg.reply_text(f"Slow down — try again in {int(wait)}s.")
            return
        last_run_now[chat.id] = now

        user = await db.get_user(chat.id)
        if user is None or not user["subscribed"]:
            await _ensure_user(
                chat.id,
                update.effective_user.username if update.effective_user else None,
            )

        await msg.reply_text("Scanning now…")
        async with run_now_lock:
            result = await daily_scan(
                db, client, http, app.bot, settings,
                scan_type="premarket", only_chat_id=chat.id,
            )
        if result["status"] == "ok":
            note = escape_md_v2(
                f"Done. {result['hits_count']} hits / {result['universe_size']} universe."
            )
            await msg.reply_text(note, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await msg.reply_text(f"Scan failed: {result.get('error', 'unknown')[:300]}")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run_now", cmd_run_now))
    app.add_handler(CommandHandler("newson", cmd_news_on))
    app.add_handler(CommandHandler("newsoff", cmd_news_off))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("universe", cmd_universe))
    app.add_handler(CommandHandler("screener", cmd_screener))
    app.add_handler(CommandHandler("threshold", cmd_threshold))
    app.add_handler(CommandHandler("frequency", cmd_frequency))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app
