from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def client_guest_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🔗 Привязать существующий код"),
                KeyboardButton(text="🆕 Регистрация нового клиента"),
            ],
            [KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
    )


def client_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Мои товары"), KeyboardButton(text="🔍 Поиск по трек-коду")],
            [KeyboardButton(text="📍 Контакты/Адрес склада"), KeyboardButton(text="🕒 График работы")],
            [KeyboardButton(text="🏠 Адрес в Китае"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="🗄 Выданные товары"), KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
    )


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Загрузить Excel"), KeyboardButton(text="🎁 Выдать товары")],
            [KeyboardButton(text="📋 Последние импорты"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="⚠️ Нераспознанные строки")],
        ],
        resize_keyboard=True,
    )
