from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from cargo_bots.bots.admin import create_admin_router
from cargo_bots.bots.client import create_client_router
from cargo_bots.core.config import Settings
from cargo_bots.services.client_service import ClientService
from cargo_bots.services.import_service import ImportService


@dataclass(slots=True)
class SingleBotRuntime:
    bot: Bot
    dispatcher: Dispatcher


@dataclass(slots=True)
class BotRuntime:
    admin_bot: Bot
    client_bot: Bot
    admin_dispatcher: Dispatcher
    client_dispatcher: Dispatcher


def create_bot_runtime(
    settings: Settings,
    client_service: ClientService,
    import_service: ImportService,
) -> BotRuntime:
    admin_runtime = create_admin_runtime(settings, import_service, client_service)
    client_runtime = create_client_runtime(settings, client_service)

    return BotRuntime(
        admin_bot=admin_runtime.bot,
        client_bot=client_runtime.bot,
        admin_dispatcher=admin_runtime.dispatcher,
        client_dispatcher=client_runtime.dispatcher,
    )


from cargo_bots.bots.middlewares.logging import LoggingMiddleware

def create_admin_runtime(settings: Settings, import_service: ImportService, client_service: ClientService) -> SingleBotRuntime:
    bot = Bot(
        settings.admin_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=_build_storage(settings))
    dispatcher.update.outer_middleware(LoggingMiddleware())
    dispatcher.include_router(create_admin_router(import_service, client_service, settings))
    return SingleBotRuntime(bot=bot, dispatcher=dispatcher)


def create_client_runtime(settings: Settings, client_service: ClientService) -> SingleBotRuntime:
    bot = Bot(
        settings.client_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=_build_storage(settings))
    dispatcher.update.outer_middleware(LoggingMiddleware())
    dispatcher.include_router(create_client_router(client_service))
    return SingleBotRuntime(bot=bot, dispatcher=dispatcher)


def _build_storage(settings: Settings):
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage
        from redis.asyncio import Redis

        return RedisStorage(redis=Redis.from_url(settings.redis_url))
    return MemoryStorage()
