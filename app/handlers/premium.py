"""Premium Handler - Premium features and promo codes"""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.services.premium_service import PremiumService
from app.keyboards.inline import premium_kb, back_to_main_kb, cancel_kb
from app.utils.formatter import format_premium_info, format_error, bold, success_message
from app.states.admin_states import PremiumStates

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "premium")
async def callback_premium(callback: CallbackQuery):
    """Show premium info"""
    text = format_premium_info()
    kb = premium_kb()
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "promo_code")
async def callback_promo_code(callback: CallbackQuery, state: FSMContext):
    """Ask for promo code"""
    await state.set_state(PremiumStates.promo_code)
    text = (
        f"🔑 {bold('Promo kod')}\n\n"
        f"Promo kodni yuboring:"
    )
    await callback.message.edit_text(text, reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(PremiumStates.promo_code)
async def process_promo_code(message: Message, state: FSMContext):
    """Process promo code input"""
    code = message.text.strip().upper()

    result = await PremiumService.redeem_promo_code(message.from_user.id, code)

    if result:
        text = success_message(
            f"Promo kod qabul qilindi! {result.premium_days} kun premium berildi."
        )
    else:
        text = format_error("invalid_link") if False else (
            f"❌ {bold('Promo kod noto\'g\'ri yoki muddati tugagan.')}\n\n"
            f"Qayta urinib ko'ring yoki yangi promo kod oling."
        )

    kb = back_to_main_kb()
    await state.clear()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
