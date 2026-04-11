from __future__ import annotations

from celery import Celery

from cargo_bots.core.config import get_settings
from celery.signals import setup_logging

settings = get_settings()

@setup_logging.connect
def on_setup_logging(**kwargs):
    from cargo_bots.core.logging import configure_logging
    configure_logging(settings)

celery_app = Celery(
    "cargo_bots",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["cargo_bots.tasks.jobs"],
)
celery_app.conf.update(
    task_always_eager=settings.task_always_eager,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
)

