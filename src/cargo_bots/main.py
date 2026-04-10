from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from cargo_bots.api.webhooks import build_router
from cargo_bots.bots.runtime import create_bot_runtime
from cargo_bots.core.config import get_settings
from cargo_bots.core.container import AppContainer
from cargo_bots.core.logging import configure_logging, configure_metrics, init_sentry
from cargo_bots.db.session import Database
from cargo_bots.services.address_book import AddressTemplateService
from cargo_bots.services.client_service import ClientService
from cargo_bots.services.excel_parser import SupplierWorkbookParser
from cargo_bots.services.import_service import ImportService
from cargo_bots.services.notification_service import NotificationService
from cargo_bots.services.storage import build_storage


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    init_sentry(settings)

    database = Database(settings)
    address_service = AddressTemplateService(settings.address_template_path)
    parser = SupplierWorkbookParser(settings.supplier_template_path)
    storage = build_storage(settings)
    client_service = ClientService(database, address_service)
    import_service = ImportService(
        database=database,
        storage=storage,
        parser=parser,
        storage_prefix=settings.storage_prefix,
    )
    runtime = create_bot_runtime(settings, client_service, import_service)
    notification_service = NotificationService(
        database=database,
        bot=runtime.client_bot,
        rate_limit_per_second=settings.bot_message_rate_limit_per_second,
    )

    container = AppContainer(
        settings=settings,
        database=database,
        runtime=runtime,
        client_service=client_service,
        import_service=import_service,
        notification_service=notification_service,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container
        if settings.auto_create_db:
            await database.create_all()

        if settings.admin_webhook_url:
            await runtime.admin_bot.set_webhook(
                url=settings.admin_webhook_url,
                secret_token=settings.admin_secret_token or None,
                drop_pending_updates=False,
            )
        if settings.client_webhook_url:
            await runtime.client_bot.set_webhook(
                url=settings.client_webhook_url,
                secret_token=settings.client_secret_token or None,
                drop_pending_updates=False,
            )

        yield

        await runtime.admin_bot.session.close()
        await runtime.client_bot.session.close()
        await _close_dispatcher_storage(runtime.admin_dispatcher)
        await _close_dispatcher_storage(runtime.client_dispatcher)
        await database.dispose()

    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    app.include_router(build_router(settings, runtime))
    configure_metrics(app, settings)
    return app


app = create_app()


async def _close_dispatcher_storage(dispatcher) -> None:
    storage = getattr(dispatcher, "storage", None)
    if storage is None and getattr(dispatcher, "fsm", None):
        storage = getattr(dispatcher.fsm, "storage", None)
    if storage is not None and hasattr(storage, "close"):
        await storage.close()
