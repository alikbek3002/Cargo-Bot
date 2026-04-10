from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def client_guest_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Привязать существующий код"),
                KeyboardButton(text="Регистрация нового клиента"),
            ],
            [KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
    )


def client_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Профиль"), KeyboardButton(text="Мои товары")],
            [KeyboardButton(text="Адрес в Китае"), KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
    )


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Загрузить Excel"), KeyboardButton(text="Последние импорты")],
            [KeyboardButton(text="Нераспознанные строки"), KeyboardButton(text="Статистика")],
        ],
        resize_keyboard=True,
    )

