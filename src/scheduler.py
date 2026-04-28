from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot

from config import Settings

from .db import Database
from .eodhd_client import EODHDClient
from .jobs import daily_scan

log = logging.getLogger(__name__)

PREMARKET_JOB_ID = "premarket_scan"
POSTMARKET_JOB_ID = "postmarket_scan"

# Back-compat for callers that still import JOB_ID (bot's /status used it).
JOB_ID = PREMARKET_JOB_ID


def build_scheduler(
    db: Database,
    client: EODHDClient,
    bot: Bot,
    settings: Settings,
) -> AsyncIOScheduler:
    tz = ZoneInfo(settings.scan_timezone)
    sched = AsyncIOScheduler(timezone=tz)

    async def _run_premarket() -> None:
        log.info("premarket scan firing")
        result = await daily_scan(db, client, bot, settings, scan_type="premarket")
        log.info("premarket scan finished: %s", result)

    async def _run_postmarket() -> None:
        log.info("postmarket scan firing")
        result = await daily_scan(db, client, bot, settings, scan_type="postmarket")
        log.info("postmarket scan finished: %s", result)

    sched.add_job(
        _run_premarket,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=settings.scan_hour,
            minute=settings.scan_minute,
            timezone=tz,
        ),
        id=PREMARKET_JOB_ID,
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        _run_postmarket,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=settings.post_scan_hour,
            minute=settings.post_scan_minute,
            timezone=tz,
        ),
        id=POSTMARKET_JOB_ID,
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    return sched
