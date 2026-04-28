from __future__ import annotations

import asyncio
import logging
import signal

import httpx

from config import Settings
from src.db import Database
from src.eodhd_client import EODHDClient
from src.scheduler import build_scheduler
from src.telegram_bot import build_telegram_app


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # APScheduler is chatty at INFO
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def run() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)
    log = logging.getLogger("main")

    if not settings.telegram_bot_token or not settings.eodhd_api_key:
        raise SystemExit("TELEGRAM_BOT_TOKEN and EODHD_API_KEY must be set")

    db = Database(settings.db_path)
    await db.connect()

    http = httpx.AsyncClient(timeout=30.0, http2=False)
    client = EODHDClient(http, settings.eodhd_api_key, db, settings.eodhd_daily_credit_cap)

    scheduler_ref: dict = {"sched": None}
    app = build_telegram_app(settings, db, client, scheduler_ref)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    sched = build_scheduler(db, client, app.bot, settings)
    sched.start()
    scheduler_ref["sched"] = sched
    log.info(
        "scheduler armed: premarket %02d:%02d, postmarket %02d:%02d %s (mon-fri)",
        settings.scan_hour, settings.scan_minute,
        settings.post_scan_hour, settings.post_scan_minute,
        settings.scan_timezone,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        log.info("shutting down")
        sched.shutdown(wait=False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await http.aclose()
        await db.close()
        log.info("shutdown complete")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
