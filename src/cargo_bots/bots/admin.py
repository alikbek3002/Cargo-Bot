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
from cargo_bots.tasks.jobs import enqueue_import_processing, flush_outbox_task


class AdminIssueStates(StatesGroup):
    waiting_for_query = State()


class AdminUploadStates(StatesGroup):
    waiting_for_days = State()


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

    # ──────── /start ────────
    @router.message(Command("start"))
    async def start_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await message.answer(
            "🤖 Админ-бот BCL EXPRESS готов!\n\n"
            "📤 Загрузить Excel — отправьте файл\n"
            "🎁 Выдать товары — выдача клиенту\n"
            "📋 Последние импорты — просмотр\n"
            "📊 Статистика — сводка",
            reply_markup=admin_keyboard(),
        )

    # ──────── Загрузить Excel ────────
    @router.message(Command("upload"))
    @router.message(F.text == "📤 Загрузить Excel")
    async def upload_help_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await message.answer("📎 Просто отправьте сюда файл .xls или .xlsx.")

    # ──────── Последние импорты ────────
    async def show_imports(message: Message) -> None:
        imports = await import_service.list_recent_imports(limit=5)
        if not imports:
            await message.answer("📭 Импортов пока нет.", reply_markup=admin_keyboard())
            return

        await message.answer("📋 Последние импорты:", reply_markup=admin_keyboard())

        for item in imports:
            se = {"COMPLETED": "✅", "PARTIAL": "⚠️", "FAILED": "❌", "PENDING": "⏳", "PROCESSING": "⏳"}
            emoji = se.get(item.status.value, "❔")

            # Укорачиваем UUID до 8 символов для callback_data (Telegram лимит 64 байта)
            short_id = str(item.id)[:8]

            text = (
                f"📄 {item.filename}\n"
                f"{emoji} Статус: {item.status.value}\n"
                f"📦 Товаров: {item.matched_rows} из {item.total_rows}\n"
                f"📅 {item.created_at.strftime('%Y-%m-%d %H:%M')}"
            )

            markup = None
            if item.status in (ImportStatus.COMPLETED, ImportStatus.PARTIAL):
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text="✅ Готов к выдаче",
                            callback_data=f"rdy:{short_id}",
                        )]
                    ]
                )

            await message.answer(text, reply_markup=markup)

    @router.message(Command("imports"))
    @router.message(F.text == "📋 Последние импорты")
    async def imports_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()
        await show_imports(message)

    @router.callback_query(F.data.startswith("rdy:"))
    async def mark_ready_handler(callback: CallbackQuery) -> None:
        if not has_admin_access(callback.from_user.id, settings.admin_ids):
            await callback.answer("🚫 Нет доступа.", show_alert=True)
            return

        short_id = callback.data.split(":")[1]

        # Найдем полный UUID по началу
        imports = await import_service.list_recent_imports(limit=20)
        import_job = None
        for imp in imports:
            if str(imp.id).startswith(short_id):
                import_job = imp
                break

        if not import_job:
            await callback.answer("❌ Импорт не найден.", show_alert=True)
            return

        try:
            count = await import_service.mark_import_as_ready(import_job.id)
            if count > 0:
                flush_outbox_task.delay()
                await callback.answer(f"✅ {count} товаров → Готов к выдаче!", show_alert=True)
                await callback.message.edit_text(
                    callback.message.text + f"\n\n✅ {count} товаров готовы к выдаче",
                    reply_markup=None,
                )
            else:
                await callback.answer("ℹ️ Нет товаров «В пути».", show_alert=True)
                await callback.message.edit_text(
                    callback.message.text + "\n\nℹ️ Все товары уже готовы",
                    reply_markup=None,
                )
        except Exception as e:
            await callback.answer(f"❌ {e}", show_alert=True)

    # ──────── Выдать товары (FSM) ────────
    @router.message(Command("issue"))
    @router.message(F.text == "🎁 Выдать товары")
    async def issue_start_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.set_state(AdminIssueStates.waiting_for_query)
        await message.answer(
            "🔍 Выдача товаров\n\n"
            "Введите код клиента (J-0001) или трек-код товара.\n"
            "Для отмены: /start",
            reply_markup=admin_keyboard(),
        )

    @router.message(AdminIssueStates.waiting_for_query)
    async def issue_search_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return

        query = (message.text or "").strip()
        if not query:
            await message.answer("⚠️ Введите код клиента или трек-код.")
            return

        # ─── Поиск по коду клиента ───
        client, all_parcels = await client_service.get_all_parcels_by_client_code(query)

        if client:
            ready = [p for p in all_parcels if p.status == ParcelStatus.READY]
            transit = [p for p in all_parcels if p.status == ParcelStatus.IN_TRANSIT]

            lines = [f"👤 {client.full_name} ({client.client_code})", ""]

            if ready:
                lines.append(f"✅ Готовы к выдаче ({len(ready)}):")
                for p in ready:
                    lines.append(f"  • {p.track_code}")
                lines.append("")

            if transit:
                lines.append(f"🚚 В пути ({len(transit)}):")
                for p in transit:
                    lines.append(f"  • {p.track_code}")
                lines.append("")

            if not all_parcels:
                lines.append("📭 Нет активных товаров.")

            markup = None
            if ready:
                # Сохраняем ID в FSM state, а не в callback_data!
                await state.update_data(
                    issue_parcel_ids=[str(p.id) for p in ready],
                )
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text=f"🎁 Выдать все ({len(ready)} шт.)",
                            callback_data="issue_ok",
                        )],
                        [InlineKeyboardButton(text="❌ Отмена", callback_data="issue_no")],
                    ]
                )

            await state.set_state(None)  # Выходим из FSM но данные сохраняются
            await message.answer("\n".join(lines), reply_markup=markup)
            return

        # ─── Поиск по трек-коду ───
        parcel = await client_service.get_parcel_by_track_code(query)
        if parcel:
            emoji, label = STATUS_DISPLAY.get(parcel.status, ("❔", parcel.status.value))

            if parcel.status == ParcelStatus.READY:
                await state.update_data(issue_parcel_ids=[str(parcel.id)])
                await state.set_state(None)
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🎁 Выдать", callback_data="issue_ok")],
                        [InlineKeyboardButton(text="❌ Отмена", callback_data="issue_no")],
                    ]
                )
                await message.answer(
                    f"📦 {parcel.track_code}\n{emoji} {label}",
                    reply_markup=markup,
                )
            elif parcel.status == ParcelStatus.ISSUED:
                await state.clear()
                await message.answer(
                    f"🎉 {parcel.track_code} — уже выдан.",
                    reply_markup=admin_keyboard(),
                )
            elif parcel.status == ParcelStatus.IN_TRANSIT:
                await state.clear()
                await message.answer(
                    f"🚚 {parcel.track_code} — ещё в пути.\n"
                    "Сначала отметьте через 📋 Последние импорты.",
                    reply_markup=admin_keyboard(),
                )
            else:
                await state.clear()
                await message.answer(
                    f"📦 {parcel.track_code} — {emoji} {label}",
                    reply_markup=admin_keyboard(),
                )
            return

        # ─── Ничего не найдено ───
        await message.answer(
            f"❌ «{query}» — не найдено.\n\n"
            "Код клиента: J-0001\n"
            "Трек-код: ISL12345678\n\n"
            "Попробуйте ещё раз или /start",
        )

    @router.callback_query(F.data == "issue_ok")
    async def issue_confirm_handler(callback: CallbackQuery, state: FSMContext) -> None:
        if not has_admin_access(callback.from_user.id, settings.admin_ids):
            await callback.answer("🚫 Нет доступа.", show_alert=True)
            return

        data = await state.get_data()
        parcel_id_strs = data.get("issue_parcel_ids", [])

        if not parcel_id_strs:
            await callback.answer("⚠️ Нет товаров для выдачи.", show_alert=True)
            return

        parcel_ids = [UUID(pid) for pid in parcel_id_strs]

        try:
            count = await client_service.mark_parcels_as_issued(parcel_ids)
            await state.clear()
            if count > 0:
                flush_outbox_task.delay()
                await callback.answer(f"🎉 Выдано {count} товаров!", show_alert=True)
                await callback.message.edit_text(
                    callback.message.text + f"\n\n🎉 Выдано: {count} шт.",
                    reply_markup=None,
                )
            else:
                await callback.answer("ℹ️ Товары уже были выданы.", show_alert=True)
                await callback.message.edit_text(
                    callback.message.text + "\n\nℹ️ Уже выданы.",
                    reply_markup=None,
                )
        except Exception as e:
            await callback.answer(f"❌ {e}", show_alert=True)

    @router.callback_query(F.data == "issue_no")
    async def issue_cancel_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer("Отменено.")
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ Отменено.",
            reply_markup=None,
        )

    # ──────── Нераспознанные строки ────────
    @router.message(Command("unmatched"))
    @router.message(F.text == "⚠️ Нераспознанные строки")
    async def unmatched_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        rows = await import_service.list_recent_unmatched_rows()
        if not rows:
            await message.answer("✅ Нераспознанных строк нет!", reply_markup=admin_keyboard())
            return

        lines = ["⚠️ Нераспознанные строки:", ""]
        for row in rows:
            lines.append(f"• Строка {row.row_number}: {row.reason}")
        await message.answer("\n".join(lines), reply_markup=admin_keyboard())

    # ──────── Статистика ────────
    @router.message(Command("stats"))
    @router.message(F.text == "📊 Статистика")
    async def stats_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        stats = await import_service.get_admin_stats()
        await message.answer(
            "📊 Статистика BCL EXPRESS:\n\n"
            f"👥 Клиентов: {stats.clients}\n"
            f"📦 Посылок: {stats.parcels}\n"
            f"📄 Импортов: {stats.imports}\n"
            f"⚠️ Нераспознанных: {stats.unmatched_rows}",
            reply_markup=admin_keyboard(),
        )

    # ──────── Приём Excel ────────
    @router.message(F.document)
    async def document_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await state.clear()

        document = message.document
        if not document.file_name or not document.file_name.lower().endswith((".xls", ".xlsx")):
            await message.answer("⚠️ Только .xls и .xlsx файлы.")
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

        await state.update_data(upload_job_id=str(import_job.id))
        await state.set_state(AdminUploadStates.waiting_for_days)

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="5 дней", callback_data="days:5"),
                    InlineKeyboardButton(text="10 дней", callback_data="days:10"),
                ],
                [
                    InlineKeyboardButton(text="12 дней", callback_data="days:12"),
                    InlineKeyboardButton(text="15 дней", callback_data="days:15"),
                ],
                [
                    InlineKeyboardButton(text="20 дней", callback_data="days:20"),
                    InlineKeyboardButton(text="30 дней", callback_data="days:30"),
                ]
            ]
        )

        await message.answer(
            f"📄 Файл: {document.file_name}\n\n"
            "🗓 За сколько дней приедет этот груз?\n"
            "Выберите вариант ниже или просто отправьте число сообщением:",
            reply_markup=markup,
        )

    async def _process_upload_days(message_or_call, state: FSMContext, bot, days: int) -> None:
        data = await state.get_data()
        job_id_str = data.get("upload_job_id")
        if not job_id_str:
            await state.clear()
            return

        job_id = UUID(job_id_str)
        # Update delivery_days via import_service or session directly
        from cargo_bots.db.models import ImportJob
        async with import_service.database.session() as session:
            job = await session.get(ImportJob, job_id)
            if job:
                job.delivery_days = days
                await session.commit()

        enqueue_import_processing(job_id)
        await state.clear()

        text = (
            "📤 Файл принят и отправлен в обработку!\n\n"
            f"⏳ Срок доставки: ~{days} дней\n"
            "Проверяйте статус через 📋 Последние импорты."
        )

        if isinstance(message_or_call, Message):
            await message_or_call.answer(text, reply_markup=admin_keyboard())
        else:
            await message_or_call.message.edit_text(text, reply_markup=None)
            await message_or_call.answer("Готово!")
            await bot.send_message(message_or_call.from_user.id, "✅ Задача добавлена в очередь.", reply_markup=admin_keyboard())

    @router.callback_query(AdminUploadStates.waiting_for_days, F.data.startswith("days:"))
    async def upload_days_callback(callback: CallbackQuery, state: FSMContext) -> None:
        days = int(callback.data.split(":")[1])
        await _process_upload_days(callback, state, callback.bot, days)

    @router.message(AdminUploadStates.waiting_for_days, F.text)
    async def upload_days_text(message: Message, state: FSMContext) -> None:
        try:
            days = int(message.text.strip())
            if days <= 0 or days > 100:
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Пожалуйста, введите число от 1 до 100.")
            return

        await _process_upload_days(message, state, message.bot, days)

    return router
