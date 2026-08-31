from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

# Explicitly include the task module. The worker is started with
# `-A app.workers.celery_app:celery_app`, so without this include Celery can
# boot successfully while scan jobs sent by the API are rejected as
# "unregistered task".
celery_app = Celery(
    "plumbus",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
)
celery_app.conf.beat_schedule = {
    "sync-plex-libraries": {
        "task": "app.workers.tasks.sync_enabled_libraries",
        "schedule": settings.PLEX_SCAN_INTERVAL_MINUTES * 60,
    },
    "refresh-plex-account": {
        "task": "app.workers.tasks.refresh_plex_account",
        "schedule": crontab(hour=3, minute=20),
    },
}
