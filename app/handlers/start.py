"""Start Handler - /start command"""

import asyncio
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from app.config import config
from app.keyboards.inline import main_menu_kb

logger = logging.getLogger(__name__)

router = Router()

_WELCOME_TEXT = (
    "Assalomu alaykum, <b>{name}</b>! 👋\n\n"
    "🤖 <b>UzVideoSaveBot</b> — ijtimoiy tarmoqlardan video yuklaydigan bot.\n\n"
    "📋 <b>Qanday ishlaydi?</b>\n"
    "1. YouTube, Instagram, TikTok va boshqa platformalardan video linkini nusxa qiling\n"
    "2. Botga yuboring\n"
    "3. Video avtomatik yuklanadi\n"
    "4. Agar audio kerak bo'lsa — MP3 tugmasini bosing\n\n"
    "📱 <b>Qo'llab-quvvatlanadigan platformalar:</b>\n"
    "• YouTube\n"
    "• Instagram (Reels, Story)\n"
    "• TikTok\n"
    "• Facebook, X (Twitter), Pinterest, Snapchat, Threads\n\n"
    "📌 Video linkini yuboring — men yuklab beraman!"
)


async def _register_user_bg(user_id: int, username: str, first_name: str):
    """Foydalanuvchini background'da ro'yxatdan o'tkazish — javob tezligiga ta'sir yo'q."""
    try:
        from app.database.connection import get_session_factory
        from app.database.repositories.user_repo import UserRepository
        session_factory = await get_session_factory()
        async with session_factory() as session:
            user_repo = UserRepository(session)
            await user_repo.get_or_create(
                user_id=user_id,
                username=username,
                first_name=first_name,
            )
    except Exception as e:
        logger.error(f"User register bg error: {e}")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """/start — bitta xabar, hech qanday qo'shimcha so'rov yo'q (admin ham oddiy foydalanuvchi kabi)."""
    await state.clear()

    name = message.from_user.first_name or "Foydalanuvchi"
    text = _WELCOME_TEXT.format(name=name)
    kb = main_menu_kb(user_id=message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

    asyncio.create_task(
        _register_user_bg(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )
    )


@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Orqaga - asosiy xabarga qaytish"""
    await state.clear()
    name = callback.from_user.first_name or "Foydalanuvchi"
    text = _WELCOME_TEXT.format(name=name)
    kb = main_menu_kb(user_id=callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()