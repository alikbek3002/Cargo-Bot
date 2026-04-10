from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request, Response

from cargo_bots.bots.runtime import BotRuntime
from cargo_bots.core.config import Settings


def build_router(settings: Settings, runtime: BotRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/webhook/admin", include_in_schema=False)
    async def admin_webhook(request: Request) -> Response:
        await _dispatch_update(
            request=request,
            bot=runtime.admin_bot,
            dispatcher=runtime.admin_dispatcher,
            secret_token=settings.admin_secret_token,
        )
        return Response(status_code=200)

    @router.post("/webhook/client", include_in_schema=False)
    async def client_webhook(request: Request) -> Response:
        await _dispatch_update(
            request=request,
            bot=runtime.client_bot,
            dispatcher=runtime.client_dispatcher,
            secret_token=settings.client_secret_token,
        )
        return Response(status_code=200)

    return router


async def _dispatch_update(
    *,
    request: Request,
    bot: Bot,
    dispatcher: Dispatcher,
    secret_token: str,
) -> None:
    incoming_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret_token and incoming_secret != secret_token:
        raise HTTPException(status_code=403, detail="Invalid secret token.")

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    await dispatcher.feed_update(bot, update)

