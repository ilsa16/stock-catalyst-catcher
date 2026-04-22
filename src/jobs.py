from __future__ import annotations

import logging
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from config import Settings

from .db import Database
from .eodhd_client import EODHDClient
from .formatter import render_digest
from .scanner import GapHit, scan_universe
from .universe import ensure_universe

log = logging.getLogger(__name__)


async def _send_digest_to(
    bot: Bot,
    chat_id: int,
    chunks: list[str],
) -> list[int]:
    """Send each chunk; return Telegram message IDs (one per chunk)."""
    ids: list[int] = []
    for chunk in chunks:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )
        ids.append(msg.message_id)
    return ids


async def _maybe_load_news(
    client: EODHDClient,
    tickers: set[str],
) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for t in tickers:
        item = await client.top_news(t)
        out[t] = (item or {}).get("link") if item else None
    return out


async def daily_scan(
    db: Database,
    client: EODHDClient,
    bot: Bot,
    settings: Settings,
    *,
    only_chat_id: int | None = None,
) -> dict:
    """
    The full scan + fan-out. When `only_chat_id` is set (used by /run_now), the
    digest is sent only to that user — but we still record the run.

    Returns a dict summary suitable for logging / direct reply.
    """
    job_id = await db.start_job_run()
    universe_size = 0
    hits_count = 0
    status = "ok"
    err: str | None = None
    try:
        tickers = await ensure_universe(
            db,
            client,
            override=settings.override_tickers or None,
            market_cap_min=settings.universe_market_cap_min,
            price_min=settings.universe_price_min,
            avg_vol_min=settings.universe_avg_vol_min,
        )
        universe_size = len(tickers)

        hits = await scan_universe(client, tickers)
        hits_count = len(hits)

        users = await db.list_subscribed_users()
        if only_chat_id is not None:
            users = [u for u in users if u["chat_id"] == only_chat_id]

        # Decide which tickers will be sent to a news-enabled user; only fetch news for those.
        news_needed: set[str] = set()
        for user in users:
            if not user["news_enabled"]:
                continue
            user_hits = [h for h in hits if h.gap_pct >= user["gap_threshold"]]
            news_needed.update(h.ticker for h in user_hits)

        news_by_ticker = await _maybe_load_news(client, news_needed) if news_needed else {}

        tz = ZoneInfo(settings.scan_timezone)
        scan_local = datetime.now(tz)

        for user in users:
            user_hits: list[GapHit] = [h for h in hits if h.gap_pct >= user["gap_threshold"]]
            chunks = render_digest(
                user_hits,
                threshold=user["gap_threshold"],
                universe_size=universe_size,
                scan_time_local=scan_local,
                news_by_ticker={t: news_by_ticker.get(t) for t in (h.ticker for h in user_hits)},
            )
            try:
                msg_ids = await _send_digest_to(bot, user["chat_id"], chunks)
            except Forbidden:
                log.info("user %s blocked the bot; auto-unsubscribing", user["chat_id"])
                await db.set_subscribed(user["chat_id"], False)
                continue
            except TelegramError as e:
                log.exception("send failed for chat=%s: %s", user["chat_id"], e)
                continue

            primary_msg_id = msg_ids[0] if msg_ids else None
            for hit in user_hits:
                await db.insert_alert(
                    job_run_id=job_id,
                    chat_id=user["chat_id"],
                    ticker=hit.ticker,
                    gap_pct=hit.gap_pct,
                    price=hit.price,
                    prior_close=hit.prior_close,
                    news_url=news_by_ticker.get(hit.ticker),
                    message_id=primary_msg_id,
                )
    except Exception as e:
        status = "error"
        err = f"{e}\n{traceback.format_exc()}"
        log.exception("daily_scan failed")
    finally:
        await db.finish_job_run(
            job_id,
            universe_size=universe_size,
            hits_count=hits_count,
            status=status,
            error=err,
        )

    return {
        "job_run_id": job_id,
        "universe_size": universe_size,
        "hits_count": hits_count,
        "status": status,
        "error": err,
    }
