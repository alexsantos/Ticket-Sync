from __future__ import annotations

import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_settings
from src.sync import run_sync_cycle

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    logger.info("Ticket-Sync starting")
    logger.info("  HR instance:   %s", settings.hr_api_base_url)
    logger.info("  Ops instance:  %s", settings.ops_api_base_url)
    logger.info("  Search config: %s", settings.search_config_path)
    logger.info("  Create config: %s", settings.create_config_path)
    logger.info("  State DB:      %s", settings.state_db_path)
    logger.info("  Sync cron:     %s", settings.sync_cron)

    logger.info("Running initial sync cycle on startup")
    try:
        run_sync_cycle()
    except Exception:
        logger.exception("Initial sync cycle failed; continuing on to the schedule")

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_sync_cycle,
        CronTrigger.from_crontab(settings.sync_cron),
        id="ticket_sync",
        name="HR->Ops ticket sync",
        max_instances=1,  # never overlap a slow cycle with the next tick
        coalesce=True,  # if ticks are missed, run once on catch-up, not N times
    )
    logger.info("Scheduler started with cron: %s", settings.sync_cron)

    def _handle_sigterm(signum, _frame):
        logger.info("Received signal %d, shutting down", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down")


if __name__ == "__main__":
    main()
