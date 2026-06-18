import asyncio
import logging
from typing import Dict

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.filter import StateFilter

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.services.download_service import DownloadService
from app.services.subscription_service import SubscriptionService
from app.keyboards.inline import quality_select_kb, back_to_main_kb
from app.utils.downloader import (
    detect_platform, is_video_url,
    cleanup_file, format_duration, format_file_size, format_view_count,
)
from app.utils.formatter import (
    format_video_info, format_video_caption,
    format_error, bold, separator,
)
from app.utils.helpers import extract_url_from_text as extract_url

logger = logging.getLogger(__name__)

router = Router()

# Video ma'lumotlarini vaqtincha saqlash
_video_cache: Dict[str, dict] = {}

# Yuklash animatsiyasi matnlari
_LOADING_STEPS_INFO = [
    "🔍 Link tekshirilmoqda...",
    "📡 Video topilmoqda...",
    "🌐 Server bilan bog'lanmoqda...",
]

_LOADING_STEPS_DOWNLOAD = [
    "⬇️ Yuklab olinmoqda...",
    "📦 Fayl yuklanmoqda...",
    "⚙️ Kodlash tekshirilmoqda...",
    "🎬 Video tayyorlanmoqda...",
    "✨ Deyarli tayyor...",
    "🚀 Yuborishga tayyorlanmoqda...",
]

_PLATFORM_EMOJI = {
    "tiktok": "🎵 TikTok",
    "instagram": "📸 Instagram",
    "youtube": "▶️ YouTube",
    "facebook": "📘 Facebook",
    "twitter": "🐦 X (Twitter)",
    "pinterest": "📌 Pinterest",
    "snapchat": "👻 Snapchat",
    "threads": "🧵 Threads",
}


async def _animate_info(bot: Bot, message: Message):
    """Link tekshirish animatsiyasi (tezkor — 3 ta bosqich)"""
    step = 0
    try:
        while True:
            await asyncio.sleep(1.5)
            step += 1
            text = _LOADING_STEPS_INFO[step % len(_LOADING_STEPS_INFO)]
            try:
                await message.edit_text(text)
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _animate_download(bot: Bot, message: Message):
    """Yuklash animatsiyasi (tezroq — har 2 soniya)"""
    step = 0
    try:
        while True:
            await asyncio.sleep(2)
            step += 1
            text = _LOADING_STEPS_DOWNLOAD[step % len(_LOADING_STEPS_DOWNLOAD)]
            try:
                await message.edit_text(text)
            except Exception:
                break
    except asyncio.CancelledError:
        pass


@router.message(StateFilter(None), ~F.text.startswith("/"))
async def handle_video_link(message: Message, state: FSMContext):
    """Foydalanuvchi video link yuborsa — info olish va sifat tanlash tugmalarini ko'rsatish"""
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

    # Loading animatsiyasi boshlash
    loading_msg = await message.answer("🔍 Link tekshirilmoqda...")
    animation_task = asyncio.create_task(_animate_info(message.bot, loading_msg))

    try:
        # Video ma'lumotlarini olish
        result = await DownloadService.process_url(url)

        if result is None:
            animation_task.cancel()
            await loading_msg.edit_text(
                format_error("download_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
            return

        # Animatsiyani to'xtatish
        animation_task.cancel()

        # Video ma'lumotlarini keshga saqlash
        video_id = result["info"].get("id", str(hash(url)))
        _video_cache[video_id] = {
            "url": url,
            "info": result["info"],
            "platform": result["platform"],
        }

        # Keshni tozalash (100 tadan ortiqsa)
        if len(_video_cache) > 100:
            oldest = list(_video_cache.keys())[:50]
            for k in oldest:
                del _video_cache[k]

        # Video ma'lumotlari matni
        text = format_video_info(result["info"], result["platform"])
        kb = quality_select_kb(video_id, result["available_qualities"])

        # Thumbnail bilan yuborish
        thumbnail_url = result["info"].get("thumbnail")
        if thumbnail_url:
            try:
                await loading_msg.delete()
                await message.answer_photo(
                    photo=thumbnail_url,
                    caption=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            except Exception:
                await loading_msg.edit_text(
                    text, reply_markup=kb, parse_mode="HTML"
                )
        else:
            await loading_msg.edit_text(
                text, reply_markup=kb, parse_mode="HTML"
            )

    except Exception as e:
        animation_task.cancel()
        logger.error(f"Video processing xatosi: {e}")
        try:
            await loading_msg.edit_text(
                format_error("server_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("quality_"))
async def handle_quality_select(callback: CallbackQuery, state: FSMContext):
    """Sifat tanlandi — video yuklab olish va yuborish.

    Animatsiya yuklash davomida ishlaydi.
    Video YUBORILADI, keyin loading O'CHIRILADI.
    Qum soat video chiqguncha turadi.
    """
    # Callback data: quality_{video_id}_{quality}
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("❌ Xatolik", show_alert=True)
        return

    video_id = parts[1]
    quality = parts[2]
    audio_only = quality == "mp3"

    # Keshdan video ma'lumotlarini olish
    video_data = _video_cache.get(video_id)
    if not video_data:
        await callback.answer("⏰ Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    url = video_data["url"]

    # Yuklash animatsiyasi boshlash
    loading_msg = await callback.message.answer("⬇️ Yuklab olinmoqda...")
    animation_task = asyncio.create_task(
        _animate_download(callback.bot, loading_msg)
    )

    try:
        # Video yuklash
        quality_str = quality if not audio_only else "720p"
        result = await DownloadService.download(
            url=url,
            quality=quality_str,
            audio_only=audio_only,
            user_id=callback.from_user.id,
        )

        if result is None:
            animation_task.cancel()
            await loading_msg.edit_text(
                format_error("download_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
            return

        file_path = result["file_path"]
        file_size_mb = result["file_size_mb"]

        # 1. Animatsiyani to'xtatamiz
        animation_task.cancel()

        # 2. Video/audio YUBORAMIZ (qum soat hali turadi)
        try:
            if audio_only:
                await callback.message.answer_audio(
                    audio=FSInputFile(file_path),
                    caption=f"🎵 MP3 Audio\n🤖 Downloader Pro",
                )
            elif file_size_mb > config.download.max_file_size_mb:
                await callback.message.answer_document(
                    document=FSInputFile(file_path),
                    caption=format_video_caption(result["info"], quality.upper()),
                )
            else:
                try:
                    await callback.message.answer_video(
                        video=FSInputFile(file_path),
                        caption=format_video_caption(result["info"], quality.upper()),
                    )
                except Exception:
                    # send_video xato bo'lsa — document sifatida
                    await callback.message.answer_document(
                        document=FSInputFile(file_path),
                        caption=format_video_caption(result["info"], quality.upper()),
                    )

        except Exception as e:
            logger.error(f"Video yuborish xatosi: {e}")
            await callback.message.answer(
                format_error("server_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
        finally:
            cleanup_file(file_path)

        # 3. Video chatda ko'rindi — loading xabarini endi o'chiramiz
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