from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

log = structlog.get_logger("bot.middleware")


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            update_id = event.update_id
            user_id = None
            username = None
            action = "unknown"

            if event.message:
                action = "message"
                user_id = event.message.from_user.id if event.message.from_user else None
                username = event.message.from_user.username if event.message.from_user else None
            elif event.callback_query:
                action = "callback_query"
                user_id = event.callback_query.from_user.id
                username = event.callback_query.from_user.username

            log.info(
                "Received update",
                update_id=update_id,
                action=action,
                user_id=user_id,
                username=username,
            )

        return await handler(event, data)
