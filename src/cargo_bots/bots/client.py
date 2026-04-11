from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from cargo_bots.bots.keyboards import client_guest_keyboard, client_menu_keyboard
from cargo_bots.db.models import ParcelStatus
from cargo_bots.services.client_service import (
    ClientAlreadyBoundError,
    ClientNotRegisteredError,
    ClientService,
    ClientValidationError,
)


class LegacyBindingState(StatesGroup):
    waiting_for_client_code = State()
    waiting_for_full_name = State()


class NewRegistrationState(StatesGroup):
    waiting_for_full_name = State()


def create_client_router(client_service: ClientService) -> Router:
    router = Router(name="client-bot")

    async def send_home(message: Message, *, greeting: str | None = None) -> None:
        client = await client_service.get_client_by_telegram_user(message.from_user.id)
        if client:
            text = greeting or (
                "Добро пожаловать в cargo-бот.\n"
                "Здесь можно смотреть профиль, адрес в Китае и статусы ваших товаров."
            )
            await message.answer(text, reply_markup=client_menu_keyboard())
            return

        text = greeting or (
            "Добро пожаловать.\n"
            "Если у вас уже есть код клиента, привяжите его. Если вы новый клиент, пройдите регистрацию."
        )
        await message.answer(text, reply_markup=client_guest_keyboard())

    @router.message(Command("start"))
    async def start_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await send_home(message)

    @router.message(F.text == "Помощь")
    async def help_handler(message: Message) -> None:
        await message.answer(
            "Команды клиента:\n"
            "• Профиль\n"
            "• Мои товары\n"
            "• Адрес в Китае\n\n"
            "Если вы старый клиент, используйте привязку по коду J-1234 и ФИО."
        )

    @router.message(F.text == "Привязать существующий код")
    async def start_legacy_binding(message: Message, state: FSMContext) -> None:
        await state.set_state(LegacyBindingState.waiting_for_client_code)
        await message.answer("Введите ваш код клиента в формате J-1234.")

    @router.message(LegacyBindingState.waiting_for_client_code)
    async def receive_legacy_code(message: Message, state: FSMContext) -> None:
        await state.update_data(client_code=message.text or "")
        await state.set_state(LegacyBindingState.waiting_for_full_name)
        await message.answer("Теперь введите ваше ФИО точно так же, как в базе старых клиентов.")

    @router.message(LegacyBindingState.waiting_for_full_name)
    async def receive_legacy_name(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        try:
            client = await client_service.bind_legacy_client(
                telegram_user_id=message.from_user.id,
                telegram_chat_id=message.chat.id,
                client_code=data["client_code"],
                full_name=message.text or "",
            )
        except (ClientValidationError, ClientAlreadyBoundError) as exc:
            await message.answer(str(exc), reply_markup=client_guest_keyboard())
        else:
            await message.answer(
                f"Профиль привязан.\nВаш код клиента: {client.client_code}\n\n"
                f"{await client_service.render_address_for_telegram_user(message.from_user.id)}",
                reply_markup=client_menu_keyboard(),
            )
        finally:
            await state.clear()

    @router.message(F.text == "Регистрация нового клиента")
    async def start_new_registration(message: Message, state: FSMContext) -> None:
        await state.set_state(NewRegistrationState.waiting_for_full_name)
        await message.answer("Введите ваше ФИО для регистрации нового профиля.")

    @router.message(NewRegistrationState.waiting_for_full_name)
    async def receive_new_name(message: Message, state: FSMContext) -> None:
        client = await client_service.register_new_client(
            telegram_user_id=message.from_user.id,
            telegram_chat_id=message.chat.id,
            full_name=message.text or "",
        )
        await state.clear()
        await message.answer(
            f"Регистрация завершена.\nВаш новый код клиента: {client.client_code}\n\n"
            f"{await client_service.render_address_for_telegram_user(message.from_user.id)}",
            reply_markup=client_menu_keyboard(),
        )

    @router.message(F.text == "Профиль")
    async def profile_handler(message: Message) -> None:
        try:
            profile = await client_service.get_profile(message.from_user.id)
        except ClientNotRegisteredError:
            await send_home(
                message,
                greeting="Сначала нужно привязать существующий код или зарегистрировать нового клиента.",
            )
            return

        await message.answer(
            "Профиль клиента\n\n"
            f"Код клиента: {profile.client_code}\n"
            f"ФИО: {profile.full_name}\n"
            f"Дата регистрации: {profile.registered_at:%Y-%m-%d %H:%M}"
        )

    @router.message(F.text == "Адрес в Китае")
    async def address_handler(message: Message) -> None:
        try:
            address = await client_service.render_address_for_telegram_user(message.from_user.id)
        except ClientNotRegisteredError:
            await send_home(
                message,
                greeting="Сначала нужно зарегистрироваться, чтобы получить персональный адрес.",
            )
            return

        await message.answer(address)

    @router.message(F.text == "Мои товары")
    async def my_parcels_handler(message: Message) -> None:
        try:
            parcels = await client_service.list_client_parcels(message.from_user.id)
        except ClientNotRegisteredError:
            await send_home(
                message,
                greeting="Сначала нужно зарегистрироваться, чтобы смотреть свои товары.",
            )
            return

        if not parcels:
            await message.answer(
                "Пока у вас нет товаров.\n"
                f"Текущий статус: {ParcelStatus.EMPTY.value} — ничего не найдено."
            )
            return

        grouped: dict[ParcelStatus, list[str]] = {
            ParcelStatus.IN_TRANSIT: [],
            ParcelStatus.READY: [],
        }
        for parcel in parcels:
            grouped.setdefault(parcel.status, []).append(parcel.track_code)

        lines = ["Ваши товары:"]
        if grouped.get(ParcelStatus.IN_TRANSIT):
            lines.append("")
            lines.append("В пути:")
            lines.extend(f"• {track}" for track in grouped[ParcelStatus.IN_TRANSIT])
        if grouped.get(ParcelStatus.READY):
            lines.append("")
            lines.append("Готовы к выдаче:")
            lines.extend(f"• {track}" for track in grouped[ParcelStatus.READY])

        await message.answer("\n".join(lines))

    @router.message(F.text == "📍 Контакты/Адрес склада")
    async def contacts_handler(message: Message) -> None:
        await message.answer(
            "📍 **Наши контакты и склад**\n\n"
            "Свяжитесь с нами для любых вопросов:\n"
            "📞 Телефон: +996 XXX XXX XXX (подставьте реальный номер)\n"
            "💬 WhatsApp: wa.me/996XXXXXXXXX\n\n"
            "Адрес склада выдачи уточняйте у менеджеров."
        )

    @router.message(F.text == "🕒 График работы")
    async def schedule_handler(message: Message) -> None:
        await message.answer(
            "🕒 **График работы:**\n\n"
            "ПН-СУББОТА С 10:00 - 19:00\n"
            "Воскресенье — выходной"
        )

    @router.message(F.text == "ℹ️ Как искать")
    async def how_to_search_handler(message: Message) -> None:
        await message.answer(
            "ℹ️ **Как искать:**\n\n"
            "• Минимум 3 символа: `ISL`\n"
            "• Точнее: `ISL-1124`\n"
            "• Чем точнее запрос — тем лучше результат.\n\n"
            "Просто отправьте текст в чат, и бот найдет совпадения среди ваших товаров."
        )

    @router.message(F.text == "💰 Оплата")
    async def payment_handler(message: Message) -> None:
        await message.answer(
            "💰 Оплата только наличными — в $ или в сомах."
        )

    @router.message(F.text == "❓ Частые вопросы")
    async def faq_handler(message: Message) -> None:
        await message.answer(
            "❓ **Частые вопросы**\n\n"
            "В этом разделе вы можете добавить ответы на популярные вопросы ваших клиентов."
        )

    @router.message()
    async def fallback_handler(message: Message) -> None:
        query = (message.text or "").strip()
        
        # Если это не похоже на поиск (коротко) или просто непонятное действие:
        if len(query) < 3:
            await send_home(message, greeting="Я вас не понял. Если вы хотите найти товар, введите минимум 3 символа из трек-кода.")
            return

        try:
            parcels = await client_service.search_client_parcels(message.from_user.id, query)
        except ClientNotRegisteredError:
            await send_home(message)
            return

        if not parcels:
            await message.answer(
                f"🤷‍♂️ по запросу `{query}` ничего не найдено среди ваших товаров.\n"
                f"Текущий статус: Товар еще не прибыл",
                parse_mode="Markdown"
            )
            return

        lines = [f"🔍 **Результаты поиска по '{query}':**", ""]
        for p in parcels:
            status_emoji = "🚚" if p.status == ParcelStatus.IN_TRANSIT else "✅"
            lines.append(f"{status_emoji} {p.track_code} — {p.status.value}")
        
        await message.answer("\n".join(lines), parse_mode="Markdown")

    return router

