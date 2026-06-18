from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def main_reply_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Main reply keyboard — faqat Profil"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="👤 Profil")

    if is_admin:
        builder.button(text="🔧 Admin panel")

    builder.adjust(1 if is_admin else 1)
    return builder.as_markup(resize_keyboard=True)