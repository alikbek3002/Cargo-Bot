from __future__ import annotations

import asyncio
from uuid import UUID

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from cargo_bots.core.config import get_settings
from cargo_bots.db.session import Database
from cargo_bots.services.excel_parser import SupplierWorkbookParser
from cargo_bots.services.import_service import ImportService
from cargo_bots.services.notification_service import NotificationService
from cargo_bots.services.storage import build_storage
from cargo_bots.tasks.celery_app import celery_app


def enqueue_import_processing(import_job_id: UUID) -> None:
    settings = get_settings()
    if settings.task_always_eager:
        process_import_job_task(str(import_job_id))
        return
    process_import_job_task.delay(str(import_job_id))


@celery_app.task(name="cargo_bots.process_import")
def process_import_job_task(import_job_id: str) -> None:
    asyncio.run(_process_import(UUID(import_job_id)))


@celery_app.task(name="cargo_bots.flush_outbox")
def flush_outbox_task(limit: int = 100) -> None:
    asyncio.run(_flush_outbox(limit=limit))


async def _process_import(import_job_id: UUID) -> None:
    settings = get_settings()
    database = Database(settings)
    storage = build_storage(settings)
    parser = SupplierWorkbookParser(settings.supplier_template_path)
    import_service = ImportService(
        database=database,
        storage=storage,
        parser=parser,
        storage_prefix=settings.storage_prefix,
    )
    bot = Bot(
        settings.client_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notification_service = NotificationService(
        database=database,
        bot=bot,
        rate_limit_per_second=settings.bot_message_rate_limit_per_second,
    )

    try:
        await import_service.process_import_job(import_job_id)
        await notification_service.flush_pending()
    finally:
        await bot.session.close()
        await database.dispose()


async def _flush_outbox(*, limit: int) -> None:
    settings = get_settings()
    database = Database(settings)
    bot = Bot(
        settings.client_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notification_service = NotificationService(
        database=database,
        bot=bot,
        rate_limit_per_second=settings.bot_message_rate_limit_per_second,
    )
    try:
        await notification_service.flush_pending(limit=limit)
    finally:
        await bot.session.close()
        await database.dispose()

