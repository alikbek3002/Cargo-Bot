from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from cargo_bots.db.models import NotificationOutbox, NotificationStatus, Parcel, ParcelStatus
from cargo_bots.db.session import Database


class NotificationService:
    def __init__(
        self,
        database: Database,
        bot: Bot,
        rate_limit_per_second: float = 25.0,
    ) -> None:
        self.database = database
        self.bot = bot
        self.rate_limit_per_second = rate_limit_per_second

    async def flush_pending(self, limit: int = 100) -> int:
        async with self.database.session() as session:
            items = list(
                (
                    await session.scalars(
                        select(NotificationOutbox)
                        .where(
                            NotificationOutbox.status == NotificationStatus.PENDING,
                            NotificationOutbox.available_at <= datetime.now(tz=UTC),
                        )
                        .options(
                            selectinload(NotificationOutbox.client),
                            selectinload(NotificationOutbox.parcel),
                        )
                        .order_by(NotificationOutbox.created_at.asc())
                        .limit(limit)
                    )
                ).all()
            )

        sent_count = 0
        for item in items:
            if not item.client.telegram_chat_id:
                await self._mark_failed(item.id, "Missing telegram_chat_id", terminal=True)
                continue

            try:
                await self.bot.send_message(
                    chat_id=item.client.telegram_chat_id,
                    text=self._render_message(item),
                )
            except TelegramAPIError as exc:
                await self._mark_failed(item.id, str(exc), terminal=False)
            else:
                await self._mark_sent(item.id)
                sent_count += 1

            if self.rate_limit_per_second > 0:
                await asyncio.sleep(1 / self.rate_limit_per_second)

        return sent_count

    async def _mark_sent(self, notification_id) -> None:
        async with self.database.session() as session:
            async with session.begin():
                item = await session.get(NotificationOutbox, notification_id)
                if not item:
                    return
                item.status = NotificationStatus.SENT
                item.sent_at = datetime.now(tz=UTC)
                if item.parcel_id:
                    parcel = await session.get(Parcel, item.parcel_id)
                    if parcel:
                        parcel.notified_at = item.sent_at

    async def _mark_failed(self, notification_id, error: str, *, terminal: bool) -> None:
        async with self.database.session() as session:
            async with session.begin():
                item = await session.get(NotificationOutbox, notification_id)
                if not item:
                    return
                item.attempts += 1
                item.last_error = error
                if terminal or item.attempts >= 5:
                    item.status = NotificationStatus.FAILED
                else:
                    item.available_at = datetime.now(tz=UTC) + timedelta(minutes=item.attempts)

    def _render_message(self, item: NotificationOutbox) -> str:
        status_value = item.payload.get("status", ParcelStatus.IN_TRANSIT.value)
        status_map = {
            ParcelStatus.EMPTY.value: "Пока ничего нет",
            ParcelStatus.IN_TRANSIT.value: "В пути",
            ParcelStatus.READY.value: "Готов к выдаче",
            ParcelStatus.ISSUED.value: "Успешно выдано",
        }
        return (
            "📦 Обновление по вашему товару\n\n"
            f"Код клиента: {item.payload.get('client_code', item.client.client_code)}\n"
            f"Трек-код: {item.payload.get('track_code', '-')}\n"
            f"Статус: {status_map.get(status_value, status_value)}"
        )
