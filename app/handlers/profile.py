"""Profile Handler - User profile management"""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.keyboards.inline import back_to_main_kb
from app.utils.formatter import bold, code, separator

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "profile")
async def callback_profile(callback: CallbackQuery):
    """Show user profile"""
    session_factory = await get_session_factory()

    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_or_create(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )

        text = (
            f"👤 {bold('Profil')}\n\n"
            f"{separator()}\n\n"
            f"🆔 ID: {code(str(user.id))}\n"
            f"👤 Ism: {user.first_name or 'N/A'}\n"
            f"📱 Username: @{user.username or 'N/A'}\n"
            f"📅 Ro'yxatdan o'tgan: {user.registered_at.strftime('%d.%m.%Y')}\n"
            f"📥 Yuklangan: {bold(str(user.downloads_count))} video\n"
        )

        kb = back_to_main_kb()

        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

    await callback.answer()