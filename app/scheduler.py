"""In-process hourly scheduler.

It runs ``run_daily`` at the top of every hour; the job itself decides who is due,
based on each subscriber's own timezone, and is idempotent, so running it more
often than necessary is harmless. On a platform with its own cron, disable this
(SCHEDULER_ENABLED=false) and POST /jobs/daily with the X-Cron-Secret header.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import SessionLocal
from app.services import run_daily

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def hourly_tick() -> None:
    with SessionLocal() as db:
        report = run_daily(db)
    log.info("daily run: %s", {k: v for k, v in report.items() if k != "details"})


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(hourly_tick, "cron", minute=5, id="daily-editions", replace_existing=True)
    _scheduler.start()
    return _scheduler
