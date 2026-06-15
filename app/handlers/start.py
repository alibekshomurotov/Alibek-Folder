"""Start Handler - /start command and main menu"""

import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.keyboards.inline import main_menu_kb
from app.utils.formatter import format_welcome
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Handle /start command — welcome xabar + Profil tugmasi"""
    await state.clear()

    # Parse referral code from deep link
    referred_by = None
    if message.text and len(message.text.split()) > 1:
        args = message.text.split()[1]
        if args.startswith("ref_"):
            ref_code = args[4:]
            try:
                session_factory = await get_session_factory()
                async with session_factory() as session:
                    user_repo = UserRepository(session)
                    from sqlalchemy import select
                    from app.database.models import User
                    result = await session.execute(
                        select(User).where(User.referral_code == ref_code)
                    )
                    referrer = result.scalar_one_or_none()
                    if referrer and referrer.id != message.from_user.id:
                        referred_by = referrer.id
            except Exception as e:
                logger.error(f"Error processing referral: {e}")

    # Register or update user
    session_factory = await get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_or_create(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            referred_by=referred_by,
        )

    # Check subscription
    unsubscribed = await SubscriptionService.get_unsubscribed_channels(
        message.bot, message.from_user.id
    )

    if unsubscribed and not config.bot.is_admin(message.from_user.id):
        from app.keyboards.inline import subscription_check_kb
        from app.utils.formatter import format_subscription_required
        text = format_subscription_required(unsubscribed)
        kb = subscription_check_kb(unsubscribed)
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    # Welcome xabar + Profil tugmasi (hamma uchun bir xil)
    text = format_welcome()
    inline_kb = main_menu_kb()

    if config.bot.is_admin(message.from_user.id):
        # Admin uchun: reply keyboard (pastdagi admin tugma) o'rnatish,
        # lekin hech qanday qo'shimcha xabar ko'rsatmaslik
        from app.keyboards.reply import admin_reply_kb
        await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")
        temp_msg = await message.answer("​", reply_markup=admin_reply_kb())
        await temp_msg.delete()
    else:
        await message.answer(text, reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")


@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Return to main menu"""
    await state.clear()
    text = format_welcome()
    kb = main_menu_kb()

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    """Check if user has subscribed to required channels"""
    is_subscribed, unsubscribed = await SubscriptionService.is_subscribed(
        callback.bot, callback.from_user.id
    )

    if is_subscribed or config.bot.is_admin(callback.from_user.id):
        text = format_welcome()
        kb = main_menu_kb()

        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        from app.keyboards.inline import subscription_check_kb
        from app.utils.formatter import format_subscription_required
        text = format_subscription_required(unsubscribed)
        kb = subscription_check_kb(unsubscribed)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")