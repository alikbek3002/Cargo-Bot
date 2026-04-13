from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from cargo_bots.bots.keyboards import client_guest_keyboard, client_menu_keyboard
from cargo_bots.db.models import Parcel, ParcelStatus
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


class TrackSearchState(StatesGroup):
    waiting_for_track_code = State()


# ────────────────────────────────────────────
#  Маппинг статусов → красивый текст + эмодзи
# ────────────────────────────────────────────
STATUS_DISPLAY = {
    ParcelStatus.EMPTY: ("⏳", "Ожидание"),
    ParcelStatus.IN_TRANSIT: ("🚚", "В пути"),
    ParcelStatus.READY: ("✅", "Готов к выдаче"),
    ParcelStatus.ISSUED: ("🎉", "Выдано"),
}

def _delivery_countdown(parcel: Parcel) -> str:
    """Вычисляет обратный отсчёт доставки (от даты импорта, на основе delivery_days)."""
    # Если дата последнего обновления статуса (import) известна - берем её
    # Но для новых посылок created_at и last_seen_at почти совпадают.
    # Если parcel был создан 10 дней назад, то отсчет идет от created_at.
    start_date = parcel.created_at
    if parcel.last_import_job and parcel.last_import_job.created_at:
        start_date = parcel.last_import_job.created_at
        
    now = datetime.now(tz=UTC)
    elapsed = (now - start_date).days
    
    # Берем delivery_days из raw_row (или 12 по умолчанию)
    delivery_days = int(parcel.raw_row.get("_delivery_days", 12)) if isinstance(parcel.raw_row, dict) else 12
    
    remaining = max(delivery_days - elapsed, 0)

    if remaining == 0:
        return "📍 Ожидается со дня на день"

    # Правильное склонение: 1 день, 2-4 дня, 5-20 дней
    if remaining % 10 == 1 and remaining % 100 != 11:
        word = "день"
    elif 2 <= remaining % 10 <= 4 and not (12 <= remaining % 100 <= 14):
        word = "дня"
    else:
        word = "дней"

    return f"⏳ ~{remaining} {word}"


def create_client_router(client_service: ClientService) -> Router:
    router = Router(name="client-bot")

    # ──────────────────────────────────────
    #  Утилита: отправить «домой»
    # ──────────────────────────────────────
    async def send_home(message: Message, *, greeting: str | None = None) -> None:
        client = await client_service.get_client_by_telegram_user(message.from_user.id)
        if client:
            text = greeting or (
                f"👋 Добро пожаловать, {client.full_name}!\n\n"
                f"Ваш код клиента: {client.client_code}\n"
                "Выберите нужное действие из меню 👇"
            )
            await message.answer(text, reply_markup=client_menu_keyboard())
            return

        text = greeting or (
            "👋 Добро пожаловать в BCL EXPRESS!\n\n"
            "Если у вас уже есть код клиента — привяжите его.\n"
            "Если вы новый клиент — пройдите регистрацию."
        )
        await message.answer(text, reply_markup=client_guest_keyboard())

    # ──────────────────────────────────────
    #  /start
    # ──────────────────────────────────────
    @router.message(Command("start"))
    async def start_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await send_home(message)

    # ──────────────────────────────────────
    #  Помощь
    # ──────────────────────────────────────
    @router.message(F.text == "❓ Помощь")
    async def help_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "📖 **Справка по боту BCL EXPRESS:**\n\n"
            "📦 **Мои товары** — список всех ваших товаров и их статусы\n"
            "🔍 **Поиск по трек-коду** — введите трек-код, чтобы узнать статус конкретного товара\n"
            "📍 **Контакты/Адрес склада** — наш адрес и WhatsApp\n"
            "🕒 **График работы** — рабочие часы\n"
            "🏠 **Адрес в Китае** — ваш персональный адрес для отправки из Китая\n"
            "👤 **Профиль** — информация о вашем аккаунте\n\n"
            "💡 Если нужна помощь, напишите нам в WhatsApp!",
            parse_mode="Markdown",
        )

    # ──────────────────────────────────────
    #  Привязка старого клиента
    # ──────────────────────────────────────
    @router.message(F.text == "🔗 Привязать существующий код")
    async def start_legacy_binding(message: Message, state: FSMContext) -> None:
        await state.set_state(LegacyBindingState.waiting_for_client_code)
        await message.answer("🔑 Введите ваш код клиента (например: J-0001):")

    @router.message(LegacyBindingState.waiting_for_client_code)
    async def receive_legacy_code(message: Message, state: FSMContext) -> None:
        await state.update_data(client_code=message.text or "")
        await state.set_state(LegacyBindingState.waiting_for_full_name)
        await message.answer("✍️ Теперь введите ваше ФИО точно так же, как в базе:")

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
            await message.answer(f"❌ {exc}", reply_markup=client_guest_keyboard())
        else:
            await message.answer(
                f"✅ Профиль успешно привязан!\n\n"
                f"Ваш код клиента: {client.client_code}\n\n"
                f"{await client_service.render_address_for_telegram_user(message.from_user.id)}",
                reply_markup=client_menu_keyboard(),
            )
        finally:
            await state.clear()

    # ──────────────────────────────────────
    #  Регистрация нового клиента
    # ──────────────────────────────────────
    @router.message(F.text == "🆕 Регистрация нового клиента")
    async def start_new_registration(message: Message, state: FSMContext) -> None:
        await state.set_state(NewRegistrationState.waiting_for_full_name)
        await message.answer("✍️ Введите ваше ФИО для регистрации:")

    @router.message(NewRegistrationState.waiting_for_full_name)
    async def receive_new_name(message: Message, state: FSMContext) -> None:
        client = await client_service.register_new_client(
            telegram_user_id=message.from_user.id,
            telegram_chat_id=message.chat.id,
            full_name=message.text or "",
        )
        await state.clear()
        await message.answer(
            f"🎉 Регистрация завершена!\n\n"
            f"Ваш новый код клиента: **{client.client_code}**\n\n"
            f"{await client_service.render_address_for_telegram_user(message.from_user.id)}",
            reply_markup=client_menu_keyboard(),
            parse_mode="Markdown",
        )

    # ──────────────────────────────────────
    #  Профиль
    # ──────────────────────────────────────
    @router.message(F.text == "👤 Профиль")
    async def profile_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        try:
            profile = await client_service.get_profile(message.from_user.id)
        except ClientNotRegisteredError:
            await send_home(
                message,
                greeting="⚠️ Сначала нужно привязать код или зарегистрироваться.",
            )
            return

        await message.answer(
            "👤 **Ваш профиль**\n\n"
            f"🔑 Код клиента: {profile.client_code}\n"
            f"📝 ФИО: {profile.full_name}\n"
            f"📅 Дата регистрации: {profile.registered_at:%Y-%m-%d %H:%M}",
            parse_mode="Markdown",
        )

    # ──────────────────────────────────────
    #  Адрес в Китае
    # ──────────────────────────────────────
    @router.message(F.text == "🏠 Адрес в Китае")
    async def address_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        try:
            address = await client_service.render_address_for_telegram_user(message.from_user.id)
        except ClientNotRegisteredError:
            await send_home(
                message,
                greeting="⚠️ Сначала зарегистрируйтесь, чтобы получить персональный адрес.",
            )
            return

        await message.answer(f"🏠 **Ваш адрес в Китае:**\n\n{address}", parse_mode="Markdown")

    # ──────────────────────────────────────
    #  Мои товары
    # ──────────────────────────────────────
    @router.message(F.text == "📦 Мои товары")
    async def my_parcels_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        try:
            parcels = await client_service.list_client_parcels(message.from_user.id)
        except ClientNotRegisteredError:
            await send_home(
                message,
                greeting="⚠️ Сначала зарегистрируйтесь, чтобы смотреть свои товары.",
            )
            return

        if not parcels:
            await message.answer(
                "📦 **Мои товары**\n\n"
                "У вас пока нет активных товаров.\n"
                "Как только товар появится в системе — вы получите уведомление! 🔔",
                parse_mode="Markdown",
            )
            return

        # Группируем по статусам, сохраняя объекты посылок
        ready_parcels = [p for p in parcels if p.status == ParcelStatus.READY]
        transit_parcels = [p for p in parcels if p.status == ParcelStatus.IN_TRANSIT]

        lines = ["📦 **Мои товары:**", ""]

        if ready_parcels:
            lines.append("✅ **Готовы к выдаче:**")
            for p in ready_parcels:
                lines.append(f"  • {p.track_code}")
            lines.append("")

        if transit_parcels:
            lines.append("🚚 **В пути:**")
            for p in transit_parcels:
                countdown = _delivery_countdown(p)
                lines.append(f"  • {p.track_code}  —  {countdown}")
            lines.append("")

        total = len(parcels)
        lines.append(f"📊 Всего товаров: {total}")

        await message.answer("\n".join(lines), parse_mode="Markdown")

    # ──────────────────────────────────────
    #  Выданные товары (Архив)
    # ──────────────────────────────────────
    @router.message(F.text == "🗄 Выданные товары")
    async def archived_parcels_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        try:
            parcels = await client_service.list_issued_parcels(message.from_user.id)
        except ClientNotRegisteredError:
            await send_home(
                message,
                greeting="⚠️ Сначала зарегистрируйтесь, чтобы смотреть историю товаров.",
            )
            return

        if not parcels:
            await message.answer(
                "🗄 **Выданные товары**\n\n"
                "В вашем архиве пока нет товаров.\n"
                "Когда вы получите свои первые товары, они отобразятся здесь.",
                parse_mode="Markdown",
            )
            return

        lines = ["🗄 **Архив товаров (последние выданные):**", ""]
        # Покажем последние 50, чтобы сообщение не обрезало
        for p in parcels[:50]:
            issued_date = p.last_seen_at.strftime('%d.%m.%Y %H:%M') if p.last_seen_at else "Неизвестно"
            lines.append(f"🎉 **{p.track_code}**\n   └ Выдан: {issued_date}")
        
        if len(parcels) > 50:
            lines.append(f"\n*Показаны последние 50 из {len(parcels)} выдач.*")

        await message.answer("\n".join(lines), parse_mode="Markdown")

    # ──────────────────────────────────────
    #  Поиск по трек-коду (FSM)
    # ──────────────────────────────────────
    @router.message(F.text == "🔍 Поиск по трек-коду")
    async def search_start_handler(message: Message, state: FSMContext) -> None:
        client = await client_service.get_client_by_telegram_user(message.from_user.id)
        if not client:
            await send_home(
                message,
                greeting="⚠️ Сначала зарегистрируйтесь, чтобы искать товары.",
            )
            return

        await state.set_state(TrackSearchState.waiting_for_track_code)
        await message.answer(
            "🔍 **Поиск по трек-коду**\n\n"
            "Введите трек-код товара, чтобы узнать его статус.\n"
            "Можно ввести полный или частичный код (минимум 3 символа).\n\n"
            "Для отмены нажмите /start",
            parse_mode="Markdown",
        )

    @router.message(TrackSearchState.waiting_for_track_code)
    async def search_track_handler(message: Message, state: FSMContext) -> None:
        query = (message.text or "").strip()

        if not query or len(query) < 3:
            await message.answer(
                "⚠️ Введите минимум 3 символа трек-кода.\n"
                "Для отмены нажмите /start"
            )
            return

        try:
            parcels = await client_service.search_client_parcels(message.from_user.id, query)
        except ClientNotRegisteredError:
            await state.clear()
            await send_home(message)
            return

        if not parcels:
            # Проверим, не существует ли такой товар у другого клиента
            existing_parcel = await client_service.get_parcel_by_track_code(query)
            if existing_parcel:
                await message.answer(
                    f"🔒 Товар с трек-кодом «{query}» найден, но он вам не принадлежит.\n\n"
                    "Вы можете видеть только свои товары.\n"
                    "Введите другой трек-код или нажмите /start для выхода.",
                )
            else:
                await message.answer(
                    f"❌ По запросу «{query}» ничего не найдено.\n\n"
                    "Возможно, товар ещё не занесён в систему.\n"
                    "Как только он появится — вы получите уведомление! 🔔\n\n"
                    "Введите другой трек-код или нажмите /start для выхода.",
                )
            return

        lines = [f"🔍 **Результаты поиска: «{query}»**", ""]
        for p in parcels:
            emoji, label = STATUS_DISPLAY.get(p.status, ("❔", p.status.value))
            if p.status == ParcelStatus.IN_TRANSIT:
                countdown = _delivery_countdown(p)
                lines.append(f"{emoji} **{p.track_code}** — {label}\n   └ {countdown}")
            else:
                lines.append(f"{emoji} **{p.track_code}** — {label}")

        lines.append("")
        lines.append("Введите ещё один трек-код или нажмите /start для выхода.")

        await message.answer("\n".join(lines), parse_mode="Markdown")

    # ──────────────────────────────────────
    #  Контакты
    # ──────────────────────────────────────
    @router.message(F.text == "📍 Контакты/Адрес склада")
    async def contacts_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "📍 **BCL EXPRESS — Контакты и склад**\n\n"
            "💬 WhatsApp: +996 777 633 674\n"
            "📲 Ссылка: wa.me/996777633674\n\n"
            "🏢 **Адрес склада:** ул. Тыныстанова 189/1",
            parse_mode="Markdown",
        )

    # ──────────────────────────────────────
    #  График работы
    # ──────────────────────────────────────
    @router.message(F.text == "🕒 График работы")
    async def schedule_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "🕒 **График работы BCL EXPRESS:**\n\n"
            "📅 ПН — СБ:  10:00 — 19:00\n"
            "🔴 ВС:  Выходной",
            parse_mode="Markdown",
        )

    # ──────────────────────────────────────
    #  Fallback — любой другой текст
    # ──────────────────────────────────────
    @router.message()
    async def fallback_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await send_home(
            message,
            greeting="🤔 Не понял вашу команду.\nВыберите действие из меню 👇",
        )

    return router
