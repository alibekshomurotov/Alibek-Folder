from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def main_reply_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Reply keyboard — faqat admin uchun Admin panel tugmasi"""
    if not is_admin:
        # Oddiy foydalanuvchi uchun menyu YO'Q
        return None

    builder = ReplyKeyboardBuilder()
    builder.button(text="🔧 Admin panel")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)