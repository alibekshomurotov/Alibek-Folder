import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardRemove

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.keyboards.inline import main_menu_kb, back_to_main_kb

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Handle /start command - bot info + Profil tugma. Admin uchun yashirin reply keyboard."""
    await state.clear()

    name = message.from_user.first_name or "Foydalanuvchi"
    is_admin = config.bot.is_admin(message.from_user.id)

    # Foydalanuvchini ro'yxatdan o'tkazish
    try:
        session_factory = await get_session_factory()
        async with session_factory() as session:
            user_repo = UserRepository(session)
            await user_repo.get_or_create(
                user_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )
    except Exception as e:
        logger.error(f"User register error: {e}")

    # Xabar matni - admin uchun ham bir xil, admin panel haqida so'z yo'q
    text = (
        f"Assalomu alaykum, <b>{name}</b>! 👋\n\n"
        f"🤖 <b>UzVideoSaveBot</b> — ijtimoiy tarmoqlardan video yuklaydigan bot.\n\n"
        f"📋 <b>Qanday ishlaydi?</b>\n"
        f"1. YouTube, Instagram, TikTok va boshqa platformalardan video linkini nusxa qiling\n"
        f"2. Botga yuboring\n"
        f"3. Video avtomatik yuklanadi\n"
        f"4. Agar audio kerak bo'lsa — MP3 tugmasini bosing\n\n"
        f"📱 <b>Qo'llab-quvvatlanadigan platformalar:</b>\n"
        f"• YouTube\n"
        f"• Instagram (Reels, Story)\n"
        f"• TikTok\n"
        f"• Facebook, X (Twitter), Pinterest, Snapchat, Threads\n\n"
        f"📌 Video linkini yuboring — men yuklab beraman!"
    )

    # INLINE Profil tugma (xabar ostida) — HAMMA uchun
    inline_kb = main_menu_kb()

    # Reply keyboard — FAQAT admin uchun (yashirin, matn yo'q)
    if is_admin:
        from app.keyboards.reply import admin_reply_kb
        reply_kb = admin_reply_kb()
    else:
        reply_kb = ReplyKeyboardRemove()

    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")
    # Admin reply keyboard alohida (Telegram cheklovi: inline va reply bir xabarda bo'la olmaydi)
    if is_admin:
        await message.answer("", reply_markup=reply_kb)


@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Orqaga - asosiy xabarga qaytish"""
    await state.clear()
    name = callback.from_user.first_name or "Foydalanuvchi"
    text = (
        f"Assalomu alaykum, <b>{name}</b>! 👋\n\n"
        f"🤖 <b>UzVideoSaveBot</b> — ijtimoiy tarmoqlardan video yuklaydigan bot.\n\n"
        f"📋 <b>Qanday ishlaydi?</b>\n"
        f"1. YouTube, Instagram, TikTok va boshqa platformalardan video linkini nusxa qiling\n"
        f"2. Botga yuboring\n"
        f"3. Video avtomatik yuklanadi\n"
        f"4. Agar audio kerak bo'lsa — MP3 tugmasini bosing\n\n"
        f"📱 <b>Qo'llab-quvvatlanadigan platformalar:</b>\n"
        f"• YouTube\n"
        f"• Instagram (Reels, Story)\n"
        f"• TikTok\n"
        f"• Facebook, X (Twitter), Pinterest, Snapchat, Threads\n\n"
        f"📌 Video linkini yuboring — men yuklab beraman!"
    )
    kb = main_menu_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()