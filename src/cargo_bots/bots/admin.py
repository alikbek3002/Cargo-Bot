from __future__ import annotations

from io import BytesIO
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from cargo_bots.bots.keyboards import admin_keyboard
from cargo_bots.core.access import has_admin_access
from cargo_bots.core.config import Settings
from cargo_bots.db.models import ImportStatus, ParcelStatus
from cargo_bots.services.client_service import ClientService
from cargo_bots.services.import_service import ImportService
from cargo_bots.tasks.jobs import enqueue_import_processing


class AdminIssueStates(StatesGroup):
    waiting_for_query = State()


# Маппинг статусов для админки
STATUS_DISPLAY = {
    ParcelStatus.EMPTY: ("⏳", "Ожидание"),
    ParcelStatus.IN_TRANSIT: ("🚚", "В пути"),
    ParcelStatus.READY: ("✅", "Готов к выдаче"),
    ParcelStatus.ISSUED: ("🎉", "Выдано"),
}


def create_admin_router(
    import_service: ImportService,
    client_service: ClientService,
    settings: Settings,
) -> Router:
    router = Router(name="admin-bot")

    # ──────────────────────────────────────
    #  Утилиты
    # ──────────────────────────────────────
    def is_admin(message: Message) -> bool:
        return has_admin_access(
            message.from_user.id if message.from_user else None,
            settings.admin_ids,
        )

    async def deny_if_needed(message: Message) -> bool:
        if is_admin(message):
            return False
        await message.answer("🚫 У вас нет доступа к админ-боту.")
        return True

    # ──────────────────────────────────────
    #  /start
    # ──────────────────────────────────────
    @router.message(Command("start"))
    async def start_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await message.answer(
            "🤖 **Админ-бот BCL EXPRESS готов!**\n\n"
            "📤 Загрузить Excel — отправьте файл\n"
            "🎁 Выдать товары — выдача клиенту\n"
            "📋 Последние импорты — просмотр и смена статуса\n"
            "📊 Статистика — общая сводка\n\n"
            "Также доступны команды: /imports, /stats, /issue",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown",
        )

    # ──────────────────────────────────────
    #  Загрузить Excel
    # ──────────────────────────────────────
    @router.message(Command("upload"))
    @router.message(F.text == "📤 Загрузить Excel")
    async def upload_help_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await message.answer("📎 Просто отправьте сюда файл формата .xls или .xlsx.")

    # ──────────────────────────────────────
    #  Последние импорты + кнопка «Готов к выдаче»
    # ──────────────────────────────────────
    async def show_imports(message: Message) -> None:
        imports = await import_service.list_recent_imports(limit=5)
        if not imports:
            await message.answer("📭 Импортов пока нет.", reply_markup=admin_keyboard())
            return

        await message.answer("📋 **Последние импорты (до 5):**", reply_markup=admin_keyboard(), parse_mode="Markdown")

        for item in imports:
            status_emoji = {"COMPLETED": "✅", "PARTIAL": "⚠️", "FAILED": "❌", "PENDING": "⏳", "PROCESSING": "⏳"}
            emoji = status_emoji.get(item.status.value, "❔")

            text = (
                f"📄 **{item.filename}**\n"
                f"{emoji} Статус: {item.status.value}\n"
                f"📦 Товаров: {item.matched_rows} из {item.total_rows}\n"
                f"📅 Загружен: {item.created_at.strftime('%Y-%m-%d %H:%M')}"
            )

            markup = None
            if item.status in (ImportStatus.COMPLETED, ImportStatus.PARTIAL):
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text="✅ Отметить «Готов к выдаче»",
                            callback_data=f"mark_ready:{item.id}",
                        )]
                    ]
                )

            await message.answer(text, reply_markup=markup, parse_mode="Markdown")

    @router.message(Command("imports"))
    @router.message(F.text == "📋 Последние импорты")
    async def imports_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await show_imports(message)

    @router.callback_query(F.data.startswith("mark_ready:"))
    async def mark_ready_handler(callback: CallbackQuery) -> None:
        if not has_admin_access(callback.from_user.id, settings.admin_ids):
            await callback.answer("🚫 У вас нет доступа.", show_alert=True)
            return

        import_job_id = UUID(callback.data.split(":")[1])

        try:
            updated_count = await import_service.mark_import_as_ready(import_job_id)
            if updated_count > 0:
                await callback.answer(
                    f"✅ {updated_count} товаров → Готов к выдаче!\nУведомления отправлены клиентам.",
                    show_alert=True,
                )
                await callback.message.edit_text(
                    callback.message.text + f"\n\n✅ Отмечено: {updated_count} товаров готовы к выдаче",
                    reply_markup=None,
                    parse_mode="Markdown",
                )
            else:
                await callback.answer(
                    "ℹ️ Нет товаров со статусом «В пути» для этого импорта.",
                    show_alert=True,
                )
                await callback.message.edit_text(
                    callback.message.text + "\n\nℹ️ Все товары уже готовы или выданы",
                    reply_markup=None,
                    parse_mode="Markdown",
                )
        except Exception as e:
            await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

    # ──────────────────────────────────────
    #  Выдать товары (FSM)
    # ──────────────────────────────────────
    @router.message(Command("issue"))
    @router.message(F.text == "🎁 Выдать товары")
    async def issue_start_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.set_state(AdminIssueStates.waiting_for_query)
        await message.answer(
            "🔍 **Выдача товаров**\n\n"
            "Введите:\n"
            "• Код клиента (например: J-0001)\n"
            "• Или трек-код товара\n\n"
            "Для отмены нажмите /start",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown",
        )

    @router.message(AdminIssueStates.waiting_for_query)
    async def issue_search_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return

        query = (message.text or "").strip()
        if not query:
            await message.answer("⚠️ Введите код клиента или трек-код.")
            return

        # ─── Попробуем найти как код клиента ───
        client, all_parcels = await client_service.get_all_parcels_by_client_code(query)

        if client:
            ready_parcels = [p for p in all_parcels if p.status == ParcelStatus.READY]
            in_transit = [p for p in all_parcels if p.status == ParcelStatus.IN_TRANSIT]

            lines = [f"👤 **Клиент: {client.full_name} ({client.client_code})**", ""]

            if ready_parcels:
                lines.append(f"✅ **Готовы к выдаче ({len(ready_parcels)}):**")
                for p in ready_parcels:
                    lines.append(f"  • {p.track_code}")
                lines.append("")

            if in_transit:
                lines.append(f"🚚 **В пути ({len(in_transit)}):**")
                for p in in_transit:
                    lines.append(f"  • {p.track_code}")
                lines.append("")

            if not all_parcels:
                lines.append("📭 Нет активных товаров у этого клиента.")

            markup = None
            if ready_parcels:
                parcel_ids = [str(p.id) for p in ready_parcels]
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text=f"🎁 Выдать все готовые ({len(ready_parcels)} шт.)",
                            callback_data=f"issue_all:{','.join(parcel_ids)}",
                        )],
                        [InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data="issue_cancel",
                        )],
                    ]
                )
            
            await message.answer("\n".join(lines), reply_markup=markup, parse_mode="Markdown")
            await state.clear()
            return

        # ─── Попробуем найти как трек-код ───
        parcel = await client_service.get_parcel_by_track_code(query)
        if parcel:
            emoji, label = STATUS_DISPLAY.get(parcel.status, ("❔", parcel.status.value))

            if parcel.status == ParcelStatus.READY:
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text="🎁 Выдать этот товар",
                            callback_data=f"issue_all:{parcel.id}",
                        )],
                        [InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data="issue_cancel",
                        )],
                    ]
                )
                await message.answer(
                    f"📦 **Найден товар:**\n\n"
                    f"• Трек-код: {parcel.track_code}\n"
                    f"• Статус: {emoji} {label}",
                    reply_markup=markup,
                    parse_mode="Markdown",
                )
            elif parcel.status == ParcelStatus.ISSUED:
                await message.answer(
                    f"🎉 Товар **{parcel.track_code}** уже был выдан.",
                    reply_markup=admin_keyboard(),
                    parse_mode="Markdown",
                )
            elif parcel.status == ParcelStatus.IN_TRANSIT:
                await message.answer(
                    f"🚚 Товар **{parcel.track_code}** ещё в пути.\n"
                    "Сначала отметьте импорт как «Готов к выдаче» через 📋 Последние импорты.",
                    reply_markup=admin_keyboard(),
                    parse_mode="Markdown",
                )
            else:
                await message.answer(
                    f"📦 Товар **{parcel.track_code}** — {emoji} {label}",
                    reply_markup=admin_keyboard(),
                    parse_mode="Markdown",
                )
            await state.clear()
            return

        # ─── Ничего не найдено ───
        await message.answer(
            f"❌ По запросу «{query}» ничего не найдено.\n\n"
            "💡 Подсказки:\n"
            "• Код клиента: J-0001\n"
            "• Трек-код: ISL12345678\n\n"
            "Попробуйте ещё раз или нажмите /start для отмены.",
        )

    @router.callback_query(F.data.startswith("issue_all:"))
    async def issue_confirm_handler(callback: CallbackQuery) -> None:
        if not has_admin_access(callback.from_user.id, settings.admin_ids):
            await callback.answer("🚫 У вас нет доступа.", show_alert=True)
            return

        ids_str = callback.data.split(":", 1)[1]
        parcel_ids = [UUID(pid) for pid in ids_str.split(",")]

        try:
            updated_count = await client_service.mark_parcels_as_issued(parcel_ids)
            if updated_count > 0:
                await callback.answer(
                    f"🎉 Выдано {updated_count} товаров!\nКлиенту отправлено уведомление.",
                    show_alert=True,
                )
                await callback.message.edit_text(
                    callback.message.text + f"\n\n🎉 **Выдано: {updated_count} шт.**",
                    reply_markup=None,
                    parse_mode="Markdown",
                )
            else:
                await callback.answer(
                    "ℹ️ Товары уже были выданы или статус изменился.",
                    show_alert=True,
                )
                await callback.message.edit_text(
                    callback.message.text + "\n\nℹ️ Товары уже были выданы.",
                    reply_markup=None,
                    parse_mode="Markdown",
                )
        except Exception as e:
            await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

    @router.callback_query(F.data == "issue_cancel")
    async def issue_cancel_handler(callback: CallbackQuery) -> None:
        await callback.answer("Отменено.")
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ Отменено.",
            reply_markup=None,
            parse_mode="Markdown",
        )

    # ──────────────────────────────────────
    #  Нераспознанные строки
    # ──────────────────────────────────────
    @router.message(Command("unmatched"))
    @router.message(F.text == "⚠️ Нераспознанные строки")
    async def unmatched_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        unmatched_rows = await import_service.list_recent_unmatched_rows()
        if not unmatched_rows:
            await message.answer("✅ Нераспознанных строк нет!", reply_markup=admin_keyboard())
            return

        lines = ["⚠️ **Последние нераспознанные строки:**", ""]
        for row in unmatched_rows:
            lines.append(f"• Строка {row.row_number}: {row.reason}")
        await message.answer("\n".join(lines), reply_markup=admin_keyboard(), parse_mode="Markdown")

    # ──────────────────────────────────────
    #  Статистика
    # ──────────────────────────────────────
    @router.message(Command("stats"))
    @router.message(F.text == "📊 Статистика")
    async def stats_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        stats = await import_service.get_admin_stats()
        await message.answer(
            "📊 **Статистика BCL EXPRESS:**\n\n"
            f"👥 Клиентов: {stats.clients}\n"
            f"📦 Посылок: {stats.parcels}\n"
            f"📄 Импортов: {stats.imports}\n"
            f"⚠️ Нераспознанных строк: {stats.unmatched_rows}",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown",
        )

    # ──────────────────────────────────────
    #  Приём Excel-файла
    # ──────────────────────────────────────
    @router.message(F.document)
    async def document_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        document = message.document
        if not document.file_name or not document.file_name.lower().endswith((".xls", ".xlsx")):
            await message.answer("⚠️ Поддерживаются только файлы .xls и .xlsx.")
            return

        file = await message.bot.get_file(document.file_id)
        buffer = BytesIO()
        await message.bot.download_file(file.file_path, destination=buffer)
        payload = buffer.getvalue()

        import_job = await import_service.create_import_job(
            uploaded_by_telegram_id=message.from_user.id,
            filename=document.file_name,
            payload=payload,
        )
        enqueue_import_processing(import_job.id)

        await message.answer(
            "📤 **Файл принят в обработку!**\n\n"
            f"📄 Файл: {document.file_name}\n"
            f"🆔 ID импорта: `{import_job.id}`\n"
            "⏳ Статус: PENDING\n\n"
            "Файл обрабатывается. Используйте 📋 Последние импорты для проверки.",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown",
        )

    return router
