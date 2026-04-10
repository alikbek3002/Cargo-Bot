from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request, Response

from cargo_bots.bots.runtime import BotRuntime, SingleBotRuntime
from cargo_bots.core.config import Settings


def build_router(settings: Settings, runtime: BotRuntime) -> APIRouter:
    router = APIRouter()
    router.include_router(build_healthcheck_router())
    router.include_router(
        build_admin_webhook_router(
            secret_token=settings.admin_secret_token,
            runtime=SingleBotRuntime(
                bot=runtime.admin_bot,
                dispatcher=runtime.admin_dispatcher,
            ),
        )
    )
    router.include_router(
        build_client_webhook_router(
            secret_token=settings.client_secret_token,
            runtime=SingleBotRuntime(
                bot=runtime.client_bot,
                dispatcher=runtime.client_dispatcher,
            ),
        )
    )
    return router


def build_healthcheck_router() -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return router


def build_admin_webhook_router(*, secret_token: str, runtime: SingleBotRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/admin", include_in_schema=False)
    async def admin_webhook(request: Request) -> Response:
        await _dispatch_update(
            request=request,
            bot=runtime.bot,
            dispatcher=runtime.dispatcher,
            secret_token=secret_token,
        )
        return Response(status_code=200)

    return router


def build_client_webhook_router(*, secret_token: str, runtime: SingleBotRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/client", include_in_schema=False)
    async def client_webhook(request: Request) -> Response:
        await _dispatch_update(
            request=request,
            bot=runtime.bot,
            dispatcher=runtime.dispatcher,
            secret_token=secret_token,
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
