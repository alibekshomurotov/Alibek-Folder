"""Reply Keyboards — faqat admin uchun"""

from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def admin_reply_kb() -> ReplyKeyboardMarkup:
    """Admin reply keyboard — faqat admin panel tugmasi"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔧 Admin panel")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)