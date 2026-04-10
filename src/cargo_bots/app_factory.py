from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from cargo_bots.api.webhooks import (
    build_admin_webhook_router,
    build_client_webhook_router,
    build_healthcheck_router,
    build_router,
)
from cargo_bots.bots.runtime import create_admin_runtime, create_bot_runtime, create_client_runtime
from cargo_bots.core.config import Settings, get_settings
from cargo_bots.core.logging import configure_logging, configure_metrics, init_sentry
from cargo_bots.db.session import Database
from cargo_bots.services.address_book import AddressTemplateService
from cargo_bots.services.client_service import ClientService
from cargo_bots.services.excel_parser import SupplierWorkbookParser
from cargo_bots.services.import_service import ImportService
from cargo_bots.services.storage import build_storage


@dataclass(slots=True)
class AppServices:
    settings: Settings
    database: Database
    client_service: ClientService
    import_service: ImportService


def create_combined_app() -> FastAPI:
    services = _build_services()
    runtime = create_bot_runtime(
        services.settings,
        services.client_service,
        services.import_service,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if services.settings.auto_create_db:
            await services.database.create_all()

        if services.settings.admin_webhook_url:
            await runtime.admin_bot.set_webhook(
                url=services.settings.admin_webhook_url,
                secret_token=services.settings.admin_secret_token or None,
                drop_pending_updates=False,
            )
        if services.settings.client_webhook_url:
            await runtime.client_bot.set_webhook(
                url=services.settings.client_webhook_url,
                secret_token=services.settings.client_secret_token or None,
                drop_pending_updates=False,
            )

        yield

        await runtime.admin_bot.session.close()
        await runtime.client_bot.session.close()
        await _close_dispatcher_storage(runtime.admin_dispatcher)
        await _close_dispatcher_storage(runtime.client_dispatcher)
        await services.database.dispose()

    app = _build_base_app(services.settings, lifespan)
    app.include_router(build_router(services.settings, runtime))
    return app


def create_admin_app() -> FastAPI:
    services = _build_services()
    runtime = create_admin_runtime(services.settings, services.import_service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if services.settings.auto_create_db:
            await services.database.create_all()

        if services.settings.admin_webhook_url:
            await runtime.bot.set_webhook(
                url=services.settings.admin_webhook_url,
                secret_token=services.settings.admin_secret_token or None,
                drop_pending_updates=False,
            )

        yield

        await runtime.bot.session.close()
        await _close_dispatcher_storage(runtime.dispatcher)
        await services.database.dispose()

    app = _build_base_app(services.settings, lifespan)
    app.include_router(build_healthcheck_router())
    app.include_router(
        build_admin_webhook_router(
            secret_token=services.settings.admin_secret_token,
            runtime=runtime,
        )
    )
    return app


def create_client_app() -> FastAPI:
    services = _build_services()
    runtime = create_client_runtime(services.settings, services.client_service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if services.settings.auto_create_db:
            await services.database.create_all()

        if services.settings.client_webhook_url:
            await runtime.bot.set_webhook(
                url=services.settings.client_webhook_url,
                secret_token=services.settings.client_secret_token or None,
                drop_pending_updates=False,
            )

        yield

        await runtime.bot.session.close()
        await _close_dispatcher_storage(runtime.dispatcher)
        await services.database.dispose()

    app = _build_base_app(services.settings, lifespan)
    app.include_router(build_healthcheck_router())
    app.include_router(
        build_client_webhook_router(
            secret_token=services.settings.client_secret_token,
            runtime=runtime,
        )
    )
    return app


def _build_services() -> AppServices:
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
    return AppServices(
        settings=settings,
        database=database,
        client_service=client_service,
        import_service=import_service,
    )


def _build_base_app(settings: Settings, lifespan) -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    configure_metrics(app, settings)
    return app


async def _close_dispatcher_storage(dispatcher) -> None:
    storage = getattr(dispatcher, "storage", None)
    if storage is None and getattr(dispatcher, "fsm", None):
        storage = getattr(dispatcher.fsm, "storage", None)
    if storage is not None and hasattr(storage, "close"):
        await storage.close()
