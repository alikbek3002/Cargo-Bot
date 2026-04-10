from __future__ import annotations

from celery import Celery

from cargo_bots.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "cargo_bots",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_always_eager=settings.task_always_eager,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
)

