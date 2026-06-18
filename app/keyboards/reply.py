"""Reply Keyboards"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def admin_reply_kb() -> ReplyKeyboardMarkup:
    """Admin uchun reply keyboard — faqat Admin panel tugmasi."""
    kb = [
        [KeyboardButton(text="🔧 Admin panel")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        one_time_keyboard=False,
    )