from __future__ import annotations

import logging

from fastapi import FastAPI

from cargo_bots.core.config import Settings


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def init_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.15,
        profiles_sample_rate=0.0,
        environment=settings.env,
    )


def configure_metrics(app: FastAPI, settings: Settings) -> None:
    if not settings.metrics_enabled:
        return

    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator().instrument(app).expose(app, include_in_schema=False)

