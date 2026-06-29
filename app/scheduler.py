"""Background scheduler that runs availability checks on an interval."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings
from .services import run_all_checks

logger = logging.getLogger(__name__)

JOB_ID = "check-all-products"


def create_scheduler(settings: Settings) -> BackgroundScheduler:
    """Build a scheduler with the recurring check job registered.

    The first run happens one interval after start, not immediately, to avoid
    a burst of requests every time the app boots.
    """
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_all_checks,
        trigger=IntervalTrigger(minutes=settings.check_interval_minutes),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler
