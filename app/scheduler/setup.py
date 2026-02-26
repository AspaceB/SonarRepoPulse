import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import AppConfig
from app.scheduler.jobs import scheduled_scan

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def configure_scheduler(config: AppConfig) -> None:
    """Register scheduled jobs from config if scheduler is enabled."""
    if not config.scheduler.enabled:
        logger.info("Scheduler is disabled in config")
        return

    for job in config.scheduler.jobs:
        parts = job.cron.split()
        if len(parts) != 5:
            logger.error(f"Invalid cron expression for job '{job.name}': {job.cron}")
            continue

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
        scheduler.add_job(
            scheduled_scan,
            trigger=trigger,
            id=job.name,
            kwargs={"providers": job.providers},
            replace_existing=True,
        )
        logger.info(f"Scheduled job '{job.name}': cron={job.cron}, providers={job.providers}")


def start_scheduler() -> None:
    if scheduler.get_jobs():
        scheduler.start()
        logger.info("Scheduler started")
    else:
        logger.info("No scheduled jobs configured, scheduler not started")


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
