"""Reply Keyboards"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def main_reply_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Main reply keyboard"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📥 Video yuklash")
    builder.button(text="👤 Profil")
    builder.button(text="⭐ Premium")
    builder.button(text="ℹ️ Yordam")

    if is_admin:
        builder.button(text="🔧 Admin panel")

    builder.adjust(2, 2, 1 if is_admin else 2)
    return builder.as_markup(resize_keyboard=True)
