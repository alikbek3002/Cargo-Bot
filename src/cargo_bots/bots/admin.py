from __future__ import annotations

from io import BytesIO
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from cargo_bots.bots.keyboards import admin_keyboard
from cargo_bots.core.access import has_admin_access
from cargo_bots.core.config import Settings
from cargo_bots.services.import_service import ImportService
from cargo_bots.tasks.jobs import enqueue_import_processing


def create_admin_router(import_service: ImportService, settings: Settings) -> Router:
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

    async def show_imports(message: Message) -> None:
        imports = await import_service.list_recent_imports(limit=5)
        if not imports:
            await message.answer("Импортов пока нет.", reply_markup=admin_keyboard())
            return
            
        await message.answer("Последние импорты (до 5 штук):", reply_markup=admin_keyboard())
        
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        for item in imports:
            text = (
                f"📄 Сундук/Импорт: **{item.filename}**\n"
                f"Статус: {item.status.value}\n"
                f"Товаров: {item.matched_rows} из {item.total_rows}\n"
                f"Дата загрузки: {item.created_at.strftime('%Y-%m-%d %H:%M')}"
            )
            
            # Только если статус НЕ Failed и НЕ Pending, можно отметить готовность
            markup = None
            if item.status in (ImportStatus.COMPLETED, ImportStatus.PARTIAL, ImportStatus.PROCESSING):
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Отметить 'Готов к выдаче'", callback_data=f"mark_ready:{item.id}")]
                    ]
                )
                
            await message.answer(text, reply_markup=markup, parse_mode="Markdown")

    @router.callback_query(F.data.startswith("mark_ready:"))
    async def mark_ready_handler(callback: aiogram.types.CallbackQuery) -> None:
        if not has_admin_access(callback.from_user.id, settings.admin_ids):
            await callback.answer("У вас нет доступа.", show_alert=True)
            return

        import_job_id_str = callback.data.split(":")[1]
        from uuid import UUID
        import_job_id = UUID(import_job_id_str)
        
        try:
            updated_count = await import_service.mark_import_as_ready(import_job_id)
            if updated_count > 0:
                await callback.answer(f"Успешно: {updated_count} товаров отмечены как готовые!", show_alert=True)
                # Убираем кнопку после успешного нажатия (опционально)
                await callback.message.edit_text(
                    callback.message.text + "\n\n✅ Отправлено на склад (Готово к выдаче)",
                    reply_markup=None
                )
            else:
                await callback.answer("Нет товаров 'В пути' для этого импорта.", show_alert=True)
                await callback.message.edit_text(
                    callback.message.text + "\n\nℹ️ Все товары уже готовы или импорт пуст.",
                    reply_markup=None
                )
        except Exception as e:
            await callback.answer(f"Ошибка: {e}", show_alert=True)

    @router.message(Command("start"))
    async def start_handler(message: Message) -> None:
        if await deny_if_needed(message):
            return
        await message.answer(
            "Админ-бот готов.\n"
            "Отправьте Excel-файл или используйте команды /imports, /unmatched, /stats.",
            reply_markup=admin_keyboard(),
        )

    @router.message(Command("upload"))
    @router.message(F.text == "Загрузить Excel")
    async def upload_help_handler(message: Message) -> None:
        if await deny_if_needed(message):
            return
        await message.answer("Просто отправьте сюда файл формата .xls или .xlsx.")

    @router.message(Command("imports"))
    @router.message(F.text == "Последние импорты")
    async def imports_handler(message: Message) -> None:
        if await deny_if_needed(message):
            return
        await show_imports(message)

    @router.message(Command("unmatched"))
    @router.message(F.text == "Нераспознанные строки")
    async def unmatched_handler(message: Message) -> None:
        if await deny_if_needed(message):
            return

        unmatched_rows = await import_service.list_recent_unmatched_rows()
        if not unmatched_rows:
            await message.answer("Нераспознанных строк пока нет.", reply_markup=admin_keyboard())
            return

        lines = ["Последние нераспознанные строки:"]
        for row in unmatched_rows:
            lines.append(f"• row={row.row_number}: {row.reason}")
        await message.answer("\n".join(lines), reply_markup=admin_keyboard())

    @router.message(Command("stats"))
    @router.message(F.text == "Статистика")
    async def stats_handler(message: Message) -> None:
        if await deny_if_needed(message):
            return

        stats = await import_service.get_admin_stats()
        await message.answer(
            "Статистика системы:\n"
            f"• Клиентов: {stats.clients}\n"
            f"• Посылок: {stats.parcels}\n"
            f"• Импортов: {stats.imports}\n"
            f"• Нераспознанных строк: {stats.unmatched_rows}",
            reply_markup=admin_keyboard(),
        )

    @router.message(F.document)
    async def document_handler(message: Message) -> None:
        if await deny_if_needed(message):
            return

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
