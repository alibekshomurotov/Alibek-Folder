

import asyncio
import logging
from typing import Dict

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.services.download_service import DownloadService
from app.services.subscription_service import SubscriptionService
from app.keyboards.inline import back_to_main_kb, mp3_download_kb
from app.utils.downloader import (
    detect_platform, is_video_url,
    cleanup_file,
)
from app.utils.formatter import (
    format_video_caption,
    format_error, bold,
)
from app.utils.helpers import extract_url_from_text as extract_url

logger = logging.getLogger(__name__)

router = Router()

# URL larni vaqtincha saqlash (MP3 uchun)
_url_cache: Dict[str, str] = {}

# Yuklash animatsiyasi matnlari
_LOADING_STEPS = [
    "⏳ Yuklab olinmoqda...",
    "⏳ Video yuklanmoqda...",
    "⏳ Fayl tayyorlanmoqda...",
    "⏳ Deyarli tayyor...",
    "⏳ Yuborishga tayyorlanmoqda...",
]


async def _animate_loading(bot: Bot, message: Message):
    """Qum soat animatsiyasi — video chiqguncha aylanaveradi."""
    step = 0
    try:
        while True:
            await asyncio.sleep(2)
            step += 1
            text = _LOADING_STEPS[step % len(_LOADING_STEPS)]
            try:
                await message.edit_text(text)
            except Exception:
                break
    except asyncio.CancelledError:
        pass


@router.message(StateFilter(None), ~F.text.startswith("/"))
async def handle_video_link(message: Message, state: FSMContext):
    """Foydalanuvchi link yuborsa — to'g'ridan-to'g'ri yuklab video yuborish.

    1. Link tekshiriladi
    2. ⏳ Qum soat animatsiyasi boshlanadi
    3. Video yuklanadi (eng yaxshi sifat)
    4. Video yuboriladi (qum soat hali turadi)
    5. Qum soat yoqoladi
    6. Video ostida MP3 tugmasi
    """
    url = extract_url(message.text or "")

    if not url:
        return

    if not is_video_url(url):
        await message.answer(
            format_error("invalid_link"),
            reply_markup=back_to_main_kb(),
            parse_mode="HTML",
        )
        return

    # Obuna tekshirish
    if not config.bot.is_admin(message.from_user.id):
        is_subscribed, unsubscribed = await SubscriptionService.is_subscribed(
            message.bot, message.from_user.id
        )
        if not is_subscribed:
            from app.keyboards.inline import subscription_check_kb
            from app.utils.formatter import format_subscription_required
            text = format_subscription_required(unsubscribed)
            kb = subscription_check_kb(unsubscribed)
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
            return

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
        logger.warning(f"Foydalanuvchi ro'yxati xatosi: {e}")

    # ⏳ Qum soat animatsiyasi boshlash
    loading_msg = await message.answer("⏳ Yuklab olinmoqda...")
    animation_task = asyncio.create_task(_animate_loading(message.bot, loading_msg))

    try:
        # Video yuklash — eng yaxshi sifatda
        result = await DownloadService.download(
            url=url,
            quality="720p",
            audio_only=False,
            user_id=message.from_user.id,
        )

        if result is None:
            animation_task.cancel()
            try:
                await loading_msg.edit_text(
                    format_error("download_error"),
                    reply_markup=back_to_main_kb(),
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        file_path = result["file_path"]
        file_size_mb = result["file_size_mb"]

        # Animatsiyani to'xtatamiz (qum soat hali ko'rinadi)
        animation_task.cancel()

        # Video YUBORAMIZ — qum soat hali turadi
        caption = format_video_caption(result["info"])
        mp3_kb = mp3_download_kb(url)

        try:
            if file_size_mb > config.download.max_file_size_mb:
                await message.answer_document(
                    document=FSInputFile(file_path),
                    caption=caption,
                    reply_markup=mp3_kb,
                )
            else:
                try:
                    await message.answer_video(
                        video=FSInputFile(file_path),
                        caption=caption,
                        reply_markup=mp3_kb,
                    )
                except Exception:
                    await message.answer_document(
                        document=FSInputFile(file_path),
                        caption=caption,
                        reply_markup=mp3_kb,
                    )
        except Exception as e:
            logger.error(f"Video yuborish xatosi: {e}")
            try:
                await message.answer(
                    format_error("server_error"),
                    reply_markup=back_to_main_kb(),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        finally:
            cleanup_file(file_path)

        # Video chatda ko'rindi — ⏳ qum soatni endi o'chiramiz
        try:
            await loading_msg.delete()
        except Exception:
            pass

    except Exception as e:
        animation_task.cancel()
        logger.error(f"Yuklash xatosi: {e}")
        try:
            await loading_msg.edit_text(
                format_error("server_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("mp3_"))
async def handle_mp3_download(callback: CallbackQuery, state: FSMContext):
    """MP3 yuklash tugmasi bosildi — audio yuklab yuborish."""
    url = callback.data[4:]  # "mp3_" olib tashlash

    if not url:
        await callback.answer("❌ URL topilmadi", show_alert=True)
        return

    await callback.answer("🎵 MP3 yuklanmoqda...")

    # ⏳ Qum soat animatsiyasi
    loading_msg = await callback.message.answer("⏳ MP3 yuklab olinmoqda...")
    animation_task = asyncio.create_task(_animate_loading(callback.bot, loading_msg))

    try:
        result = await DownloadService.download(
            url=url,
            quality="720p",
            audio_only=True,
            user_id=callback.from_user.id,
        )

        if result is None:
            animation_task.cancel()
            try:
                await loading_msg.edit_text(
                    "❌ MP3 yuklab bo'lmadi.",
                    reply_markup=back_to_main_kb(),
                )
            except Exception:
                pass
            return

        file_path = result["file_path"]

        # Animatsiyani to'xtatamiz
        animation_task.cancel()

        # Audio YUBORAMIZ — qum soat hali turadi
        try:
            await callback.message.answer_audio(
                audio=FSInputFile(file_path),
                caption="🎵 MP3 Audio\n🤖 @UzVideoSaveBot",
            )
        except Exception as e:
            logger.error(f"Audio yuborish xatosi: {e}")
        finally:
            cleanup_file(file_path)

        # Audio chatda ko'rindi — qum soatni o'chiramiz
        try:
            await loading_msg.delete()
        except Exception:
            pass

    except Exception as e:
        animation_task.cancel()
        logger.error(f"MP3 yuklash xatosi: {e}")
        try:
            await loading_msg.edit_text(
                "❌ MP3 yuklash xatosi.",
                reply_markup=back_to_main_kb(),
            )
        except Exception:
            pass


@router.callback_query(F.data == "cancel_download")
async def cancel_download(callback: CallbackQuery, state: FSMContext):
    """Yuklashni bekor qilish"""
    await callback.message.edit_text(
        "❌ Yuklash bekor qilindi.",
        reply_markup=back_to_main_kb(),
    )


@router.callback_query(F.data == "download")
async def callback_download(callback: CallbackQuery):
    """Download tugmasi bosildi — platformalar ro'yxatini ko'rsatish"""
    await callback.message.edit_text(
        f"📥 {bold('Video yuklash')}\n\n"
        f"Ijtimoiy tarmoqdan video linkini yuboring.\n\n"
        f"📱 Qo'llab-quvvatlanadi:\n"
        f"  🎵 TikTok\n"
        f"  📸 Instagram\n"
        f"  ▶️ YouTube\n"
        f"  📘 Facebook\n"
        f"  🐦 X (Twitter)\n"
        f"  📌 Pinterest\n"
        f"  👻 Snapchat\n"
        f"  🧵 Threads",
        reply_markup=back_to_main_kb(),
        parse_mode="HTML",
    )