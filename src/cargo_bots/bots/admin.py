from __future__ import annotations

import aiogram
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
from cargo_bots.db.models import ImportStatus
from cargo_bots.services.client_service import ClientService
from cargo_bots.services.import_service import ImportService
from cargo_bots.tasks.jobs import enqueue_import_processing


class AdminIssueStates(StatesGroup):
    waiting_for_client_code = State()


def create_admin_router(
    import_service: ImportService,
    client_service: ClientService,
    settings: Settings,
) -> Router:
    router = Router(name="admin-bot")

    def is_admin(message: Message) -> bool:
        return has_admin_access(
            message.from_user.id if message.from_user else None,
            settings.admin_ids,
        )

    async def deny_if_needed(message: Message) -> bool:
        if is_admin(message):
            return False
        await message.answer("У вас нет доступа к админ-боту.")
        return True

    # ──────────────────────────────────────────────
    #  /start
    # ──────────────────────────────────────────────
    @router.message(Command("start"))
    async def start_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await message.answer(
            "Админ-бот готов.\n"
            "Отправьте Excel-файл или используйте команды /imports, /unmatched, /stats.",
            reply_markup=admin_keyboard(),
        )

    # ──────────────────────────────────────────────
    #  Загрузить файл
    # ──────────────────────────────────────────────
    @router.message(Command("upload"))
    @router.message(F.text == "Загрузить Excel")
    async def upload_help_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await message.answer("Просто отправьте сюда файл формата .xls или .xlsx.")

    # ──────────────────────────────────────────────
    #  Последние импорты  +  кнопка «Готов к выдаче»
    # ──────────────────────────────────────────────
    async def show_imports(message: Message) -> None:
        imports = await import_service.list_recent_imports(limit=5)
        if not imports:
            await message.answer("Импортов пока нет.", reply_markup=admin_keyboard())
            return

        await message.answer("Последние импорты (до 5 штук):", reply_markup=admin_keyboard())

        for item in imports:
            text = (
                f"📄 Импорт: {item.filename}\n"
                f"Статус: {item.status.value}\n"
                f"Товаров: {item.matched_rows} из {item.total_rows}\n"
                f"Дата загрузки: {item.created_at.strftime('%Y-%m-%d %H:%M')}"
            )

            markup = None
            if item.status in (ImportStatus.COMPLETED, ImportStatus.PARTIAL, ImportStatus.PROCESSING):
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text="✅ Отметить 'Готов к выдаче'",
                            callback_data=f"mark_ready:{item.id}",
                        )]
                    ]
                )

            await message.answer(text, reply_markup=markup)

    @router.message(Command("imports"))
    @router.message(F.text == "Последние импорты")
    async def imports_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await show_imports(message)

    @router.callback_query(F.data.startswith("mark_ready:"))
    async def mark_ready_handler(callback: CallbackQuery) -> None:
        if not has_admin_access(callback.from_user.id, settings.admin_ids):
            await callback.answer("У вас нет доступа.", show_alert=True)
            return

        import_job_id = UUID(callback.data.split(":")[1])

        try:
            updated_count = await import_service.mark_import_as_ready(import_job_id)
            if updated_count > 0:
                await callback.answer(
                    f"Успешно: {updated_count} товаров отмечены как готовые!",
                    show_alert=True,
                )
                await callback.message.edit_text(
                    callback.message.text + "\n\n✅ Готово к выдаче",
                    reply_markup=None,
                )
            else:
                await callback.answer(
                    "Нет товаров 'В пути' для этого импорта.",
                    show_alert=True,
                )
                await callback.message.edit_text(
                    callback.message.text + "\n\nℹ️ Все товары уже готовы или импорт пуст.",
                    reply_markup=None,
                )
        except Exception as e:
            await callback.answer(f"Ошибка: {e}", show_alert=True)

    # ──────────────────────────────────────────────
    #  Выдать товары  (FSM-flow)
    # ──────────────────────────────────────────────
    @router.message(Command("issue"))
    @router.message(F.text == "Выдать товары")
    async def issue_start_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.set_state(AdminIssueStates.waiting_for_client_code)
        await message.answer(
            "🔍 Введите код клиента (например J-0012) или трек-код товара:",
            reply_markup=admin_keyboard(),
        )

    @router.message(AdminIssueStates.waiting_for_client_code)
    async def issue_search_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return

        query = (message.text or "").strip()
        if not query:
            await message.answer("Пожалуйста, введите код клиента или трек-код.")
            return

        # Попробуем найти как код клиента (J-xxxx)
        parcels = await client_service.get_ready_parcels_by_client_code(query)

        if parcels:
            # Нашли по коду клиента
            lines = [f"📦 Готовые к выдаче товары клиента {query}:", ""]
            for p in parcels:
                lines.append(f"• {p.track_code}")

            parcel_ids = [str(p.id) for p in parcels]

            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"✅ Выдать все ({len(parcels)} шт.)",
                        callback_data=f"issue_all:{','.join(parcel_ids)}",
                    )],
                    [InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data="issue_cancel",
                    )],
                ]
            )

            await message.answer("\n".join(lines), reply_markup=markup)
            await state.clear()
            return

        # Попробуем найти как трек-код
        parcel = await client_service.get_parcel_by_track_code(query)
        if parcel:
            from cargo_bots.db.models import ParcelStatus
            if parcel.status == ParcelStatus.READY:
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text="✅ Выдать этот товар",
                            callback_data=f"issue_all:{parcel.id}",
                        )],
                        [InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data="issue_cancel",
                        )],
                    ]
                )
                await message.answer(
                    f"📦 Найден товар:\n• Трек-код: {parcel.track_code}\n• Статус: Готов к выдаче",
                    reply_markup=markup,
                )
            elif parcel.status == ParcelStatus.ISSUED:
                await message.answer(
                    f"ℹ️ Товар {parcel.track_code} уже был выдан ранее.",
                    reply_markup=admin_keyboard(),
                )
            elif parcel.status == ParcelStatus.IN_TRANSIT:
                await message.answer(
                    f"🚚 Товар {parcel.track_code} ещё в пути. Сначала отметьте его как 'Готов к выдаче'.",
                    reply_markup=admin_keyboard(),
                )
            else:
                await message.answer(
                    f"ℹ️ Товар {parcel.track_code} — статус: {parcel.status.value}",
                    reply_markup=admin_keyboard(),
                )
            await state.clear()
            return

        # Ничего не нашли
        await message.answer(
            f"❌ По запросу «{query}» ничего не найдено.\n"
            "Проверьте код клиента или трек-код и попробуйте ещё раз.\n\n"
            "Для отмены нажмите /start",
        )

    @router.callback_query(F.data.startswith("issue_all:"))
    async def issue_confirm_handler(callback: CallbackQuery) -> None:
        if not has_admin_access(callback.from_user.id, settings.admin_ids):
            await callback.answer("У вас нет доступа.", show_alert=True)
            return

        ids_str = callback.data.split(":", 1)[1]
        parcel_ids = [UUID(pid) for pid in ids_str.split(",")]

        try:
            updated_count = await client_service.mark_parcels_as_issued(parcel_ids)
            if updated_count > 0:
                await callback.answer(
                    f"✅ Выдано {updated_count} товаров! Клиенту отправлено уведомление.",
                    show_alert=True,
                )
                await callback.message.edit_text(
                    callback.message.text + f"\n\n✅ Выдано ({updated_count} шт.)",
                    reply_markup=None,
                )
            else:
                await callback.answer(
                    "Товары уже были выданы или статус изменился.",
                    show_alert=True,
                )
                await callback.message.edit_text(
                    callback.message.text + "\n\nℹ️ Товары уже были выданы.",
                    reply_markup=None,
                )
        except Exception as e:
            await callback.answer(f"Ошибка: {e}", show_alert=True)

    @router.callback_query(F.data == "issue_cancel")
    async def issue_cancel_handler(callback: CallbackQuery) -> None:
        await callback.answer("Отменено.")
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ Отменено.",
            reply_markup=None,
        )

    # ──────────────────────────────────────────────
    #  Нераспознанные строки
    # ──────────────────────────────────────────────
    @router.message(Command("unmatched"))
    @router.message(F.text == "Нераспознанные строки")
    async def unmatched_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        unmatched_rows = await import_service.list_recent_unmatched_rows()
        if not unmatched_rows:
            await message.answer("Нераспознанных строк пока нет.", reply_markup=admin_keyboard())
            return

        lines = ["Последние нераспознанные строки:"]
        for row in unmatched_rows:
            lines.append(f"• row={row.row_number}: {row.reason}")
        await message.answer("\n".join(lines), reply_markup=admin_keyboard())

    # ──────────────────────────────────────────────
    #  Статистика
    # ──────────────────────────────────────────────
    @router.message(Command("stats"))
    @router.message(F.text == "Статистика")
    async def stats_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        stats = await import_service.get_admin_stats()
        await message.answer(
            "Статистика системы:\n"
            f"• Клиентов: {stats.clients}\n"
            f"• Посылок: {stats.parcels}\n"
            f"• Импортов: {stats.imports}\n"
            f"• Нераспознанных строк: {stats.unmatched_rows}",
            reply_markup=admin_keyboard(),
        )

    # ──────────────────────────────────────────────
    #  Приём Excel-файла
    # ──────────────────────────────────────────────
    @router.message(F.document)
    async def document_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        document = message.document
        if not document.file_name or not document.file_name.lower().endswith((".xls", ".xlsx")):
            await message.answer("Поддерживаются только файлы .xls и .xlsx.")
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
            "Файл принят в обработку.\n"
            f"Импорт: {import_job.id}\n"
            f"Файл: {document.file_name}\n"
            "Статус: PENDING",
            reply_markup=admin_keyboard(),
        )

    return router
