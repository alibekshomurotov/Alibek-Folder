"""Profile Handler - User profile management"""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.keyboards.inline import profile_kb, back_to_main_kb
from app.utils.formatter import format_profile

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

        text = format_profile(user)
        kb = profile_kb()

        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "referral_link")
async def callback_referral_link(callback: CallbackQuery):
    """Show referral link"""
    session_factory = await get_session_factory()

    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_id(callback.from_user.id)

        if not user:
            await callback.answer("❌ Foydalanuvchi topilmadi", show_alert=True)
            return

        from app.utils.formatter import bold, code, separator
        text = (
            f"🔗 {bold('Taklif linki')}\n\n"
            f"{separator()}\n\n"
            f"Quyidagi link orqali do'stlaringizni taklif qiling:\n\n"
            f"{code(user.referral_link)}\n\n"
            f"{separator()}\n\n"
            f"👥 Taklif qilingan: {bold(str(user.referrals_count))} kishi"
        )

        kb = back_to_main_kb()
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
