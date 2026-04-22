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

JOB_ID = "daily_scan"


def build_scheduler(
    db: Database,
    client: EODHDClient,
    bot: Bot,
    settings: Settings,
) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=ZoneInfo(settings.scan_timezone))

    async def _run() -> None:
        log.info("scheduled scan firing")
        result = await daily_scan(db, client, bot, settings)
        log.info("scheduled scan finished: %s", result)

    sched.add_job(
        _run,
        trigger=CronTrigger(
            hour=settings.scan_hour,
            minute=settings.scan_minute,
            timezone=ZoneInfo(settings.scan_timezone),
        ),
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    return sched
