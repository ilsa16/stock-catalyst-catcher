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
from .scanner import GAP_VS_PREV_CLOSE, GAP_VS_TODAY_OPEN, GapHit, scan_universe
from .universe import (
    SCREENER_TIERS,
    UNIVERSE_WATCHLIST,
    resolve_union_for_users,
    resolve_user_universe,
)

log = logging.getLogger(__name__)

_NYC = ZoneInfo("America/New_York")


def _gap_baseline_for(scan_time_local: datetime) -> str:
    """
    Pick the gap denominator based on which US session the scan is happening in.

      - 09:30–16:00 ET (regular session) → today's open. The trader-conventional
        "intraday gap" that surfaces breakouts during the day.
      - any other time (pre-market / post-market / overnight) → previous regular
        close. Captures overnight news gappers in pre-market and total-day
        moves in post-market.

    Auto-selection means a /run_now at 11 AM ET measures intraday breakouts off
    today's open without the user having to flip a flag.
    """
    et = scan_time_local.astimezone(_NYC)
    minutes = et.hour * 60 + et.minute
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return GAP_VS_TODAY_OPEN
    return GAP_VS_PREV_CLOSE


def _ws_collect_seconds_for(scan_time_local: datetime, settings: Settings) -> float:
    """
    Decide how long to listen on the EODHD WebSocket for the current scan,
    based on what session ET is in:

      - 04:00–09:30 ET (pre-market) → settings.ws_collect_seconds
      - 16:00–20:00 ET (post-market) → settings.ws_collect_seconds
      - regular session 09:30–16:00 → 0 (HTTP /us-quote-delayed is fresh enough)
      - other (overnight) → 0 (no live trades to capture)

    Returning 0 is the kill-switch — scan_universe skips the WS overlay
    entirely. Set settings.ws_collect_seconds=0 in env to disable globally.
    """
    if settings.ws_collect_seconds <= 0:
        return 0.0
    et = scan_time_local.astimezone(_NYC)
    minutes = et.hour * 60 + et.minute
    in_premarket = 4 * 60 <= minutes < 9 * 60 + 30
    in_postmarket = 16 * 60 <= minutes < 20 * 60
    if in_premarket or in_postmarket:
        return float(settings.ws_collect_seconds)
    return 0.0


async def _send_digest_to(
    bot: Bot,
    chat_id: int,
    chunks: list[str],
) -> list[int]:
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
    scan_type: str = "premarket",
    only_chat_id: int | None = None,
) -> dict:
    """
    Resolve the union of subscribed users' universes, quote it once, filter per
    user, and fan out MarkdownV2 digests.

    scan_type selects which opt-in flag filters the user list ('premarket' or
    'postmarket'). For `only_chat_id` (from /run_now) we ignore the opt-in filter
    and scan just that user's universe.
    """
    job_id = await db.start_job_run(scan_type=scan_type)
    universe_size = 0
    hits_count = 0
    status = "ok"
    err: str | None = None
    try:
        if only_chat_id is not None:
            user = await db.get_user(only_chat_id)
            users = [user] if user is not None else []
        else:
            users = await db.list_subscribed_users(scan_type=scan_type)

        if not users:
            log.info("no subscribed users for %s; nothing to do", scan_type)
            return {
                "job_run_id": job_id,
                "universe_size": 0,
                "hits_count": 0,
                "status": "ok",
                "error": None,
            }

        tickers = await resolve_union_for_users(db, client, users)
        universe_size = len(tickers)

        tz = ZoneInfo(settings.scan_timezone)
        scan_local = datetime.now(tz)
        ws_seconds = _ws_collect_seconds_for(scan_local, settings)
        gap_baseline = _gap_baseline_for(scan_local)

        hits: list[GapHit] = (
            await scan_universe(
                client, tickers,
                max_age_seconds=settings.max_hit_age_hours * 3600,
                ws_collect_seconds=ws_seconds,
                ws_api_key=settings.eodhd_api_key if ws_seconds > 0 else None,
                gap_baseline=gap_baseline,
            )
            if tickers else []
        )
        hits_count = len(hits)
        hits_by_ticker: dict[str, GapHit] = {h.ticker: h for h in hits}

        # Fetch news only for tickers that will be shown to at least one news-enabled user.
        news_needed: set[str] = set()
        per_user_hits: list[tuple[dict, list[GapHit]]] = []
        for user in users:
            user_universe = set(
                await resolve_user_universe(
                    db, client,
                    choice=user["universe_choice"],
                    tier=user["screener_tier"],
                    chat_id=user["chat_id"],
                )
            )
            user_hits = [
                h for h in hits
                if h.ticker in user_universe and h.gap_pct >= user["gap_threshold"]
            ]
            # Apply the user's screener tier as a post-filter on top of any
            # universe choice except Watchlist. Watchlist is an explicit user
            # list — they want every name in it regardless of MCap/price/ADV.
            # For everything else (all_indices, sp500, ndx, dj30, r1000, r2000,
            # custom), the tier acts as a quality gate so e.g. "Large-cap"
            # actually drops sub-$10 names when scanning the Russell 2000.
            if user["universe_choice"] != UNIVERSE_WATCHLIST:
                tier_spec = SCREENER_TIERS.get(user["screener_tier"]) or SCREENER_TIERS["default"]
                user_hits = [
                    h for h in user_hits
                    if h.price >= tier_spec["price_min"]
                    and (h.market_cap or 0) >= tier_spec["market_cap_min"]
                    and (h.avg_volume or 0) >= tier_spec["avg_vol_min"]
                ]
            per_user_hits.append((user, user_hits))
            if user["news_enabled"]:
                news_needed.update(h.ticker for h in user_hits)

        news_by_ticker = await _maybe_load_news(client, news_needed) if news_needed else {}

        for user, user_hits in per_user_hits:
            chunks = render_digest(
                user_hits,
                threshold=user["gap_threshold"],
                universe_size=universe_size,
                scan_time_local=scan_local,
                scan_type=scan_type,
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
