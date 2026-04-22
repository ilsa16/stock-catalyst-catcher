from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from config import Settings

from .db import Database
from .eodhd_client import EODHDClient
from .formatter import escape_md_v2, render_status
from .jobs import daily_scan
from .scheduler import JOB_ID

log = logging.getLogger(__name__)

RUN_NOW_COOLDOWN_SECONDS = 5 * 60

HELP_TEXT = (
    "*Catalyst Catcher*\n"
    "/start — subscribe\n"
    "/stop — pause alerts\n"
    "/status — show prefs and next scheduled run\n"
    "/run\\_now — trigger an ad\\-hoc scan \\(rate\\-limited\\)\n"
    "/newson — attach top news headline to each hit\n"
    "/newsoff — disable news lookup\n"
    "/help — this help"
)


def build_telegram_app(
    settings: Settings,
    db: Database,
    client: EODHDClient,
    scheduler_ref: dict,
) -> Application:
    """
    `scheduler_ref` is a one-key dict {"sched": AsyncIOScheduler|None} populated by
    main() after scheduler.start(). We pass a ref because the bot is built before
    the scheduler so the bot can hand its `Bot` instance to the scheduled job.
    """
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    last_run_now: dict[int, float] = {}
    run_now_lock = asyncio.Lock()

    async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if msg is None or chat is None:
            return
        await db.upsert_user(
            chat_id=chat.id,
            username=(user.username if user else None),
            default_threshold=settings.default_gap_threshold,
        )
        await msg.reply_text(
            "Subscribed. You'll get a digest every weekday at "
            f"{settings.scan_hour:02d}:{settings.scan_minute:02d} {settings.scan_timezone}.\n"
            "Use /help to see commands."
        )

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
        user = await db.get_user(chat.id)
        if user is None:
            await msg.reply_text("Not subscribed. /start to subscribe.")
            return

        next_run_local: datetime | None = None
        sched: AsyncIOScheduler | None = scheduler_ref.get("sched")
        if sched is not None:
            job = sched.get_job(JOB_ID)
            if job is not None and job.next_run_time is not None:
                next_run_local = job.next_run_time.astimezone(ZoneInfo(settings.scan_timezone))

        last = await db.latest_job_run()
        text = render_status(
            subscribed=bool(user["subscribed"]),
            threshold=float(user["gap_threshold"]),
            news_enabled=bool(user["news_enabled"]),
            next_run_local=next_run_local,
            last_run=dict(last) if last else None,
        )
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_run_now(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return

        # Cooldown per chat
        now = time.monotonic()
        prev = last_run_now.get(chat.id, 0.0)
        wait = RUN_NOW_COOLDOWN_SECONDS - (now - prev)
        if wait > 0:
            await msg.reply_text(f"Slow down — try again in {int(wait)}s.")
            return
        last_run_now[chat.id] = now

        # Make sure user is registered before we send to them
        user = await db.get_user(chat.id)
        if user is None or not user["subscribed"]:
            await db.upsert_user(
                chat_id=chat.id,
                username=(update.effective_user.username if update.effective_user else None),
                default_threshold=settings.default_gap_threshold,
            )

        await msg.reply_text("Scanning now…")
        async with run_now_lock:
            result = await daily_scan(db, client, app.bot, settings, only_chat_id=chat.id)
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
    return app
