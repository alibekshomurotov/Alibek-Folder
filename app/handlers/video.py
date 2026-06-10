import asyncio
import logging
import os
import subprocess
import tempfile
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
from app.keyboards.inline import quality_select_kb, back_to_main_kb
from app.utils.downloader import (
    detect_platform, is_video_url,
    format_file_size, cleanup_file,
)
from app.utils.formatter import (
    format_video_info, format_video_caption, format_loading_step,
    format_error, bold,
)
from app.utils.helpers import extract_url_from_text as extract_url

logger = logging.getLogger(__name__)

router = Router()

# Store video info temporarily (in production, use Redis)
_video_cache: Dict[str, dict] = {}


def _ensure_mp4(file_path: str) -> str:
    """Fayl mp4 formatida ekanligini ta'minlash.

    Telegram faqat mp4 formatidagi videolarni to'g'ri ko'rsatadi
    va gallereyaga saqlash imkonini beradi. Agar fayl boshqa formatda
    bo'lsa (webm, mkv va h.k.), ffmpeg bilan mp4 ga o'tkazamiz.
    """
    if not os.path.exists(file_path):
        return file_path

    # Fayl kengaytmasini tekshirish
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".mp4":
        return file_path

    # Fayl allaqachon mp4 bo'lishi mumkin (kengaytma noto'g'ri)
    # FFmpeg bor bo'lsa, tekshiramiz
    if not config.download.ffmpeg_available:
        # FFmpeg yo'q, fayl nomini o'zgartiramiz
        new_path = file_path.rsplit(".", 1)[0] + ".mp4"
        os.rename(file_path, new_path)
        return new_path

    try:
        # FFmpeg bilan mp4 ga konvertatsiya
        new_path = file_path.rsplit(".", 1)[0] + ".mp4"
        logger.info(f"[Video] Konvertatsiya: {ext} → mp4")

        result = subprocess.run(
            ["ffmpeg", "-i", file_path, "-c:v", "libx264",
             "-c:a", "aac", "-movflags", "+faststart",
             "-preset", "fast", "-crf", "28",
             "-y", new_path],
            capture_output=True, timeout=60
        )

        if result.returncode == 0 and os.path.exists(new_path):
            # Eski faylni o'chirish
            try:
                os.remove(file_path)
            except OSError:
                pass
            logger.info("[Video] Konvertatsiya muvaffaqiyatli")
            return new_path
        else:
            logger.warning(f"[Video] Konvertatsiya xatosi, fayl nomi o'zgartiriladi")
            # Konvertatsiya muvaffaqiyatsiz - faqat nomini o'zgartiramiz
            if not os.path.exists(new_path):
                os.rename(file_path, new_path)
            return new_path

    except subprocess.TimeoutExpired:
        logger.warning("[Video] Konvertatsiya timeout, fayl nomi o'zgartiriladi")
        new_path = file_path.rsplit(".", 1)[0] + ".mp4"
        if not os.path.exists(new_path):
            os.rename(file_path, new_path)
        return new_path
    except Exception as e:
        logger.warning(f"[Video] Konvertatsiya xatosi: {e}")
        new_path = file_path.rsplit(".", 1)[0] + ".mp4"
        if not os.path.exists(new_path):
            os.rename(file_path, new_path)
        return new_path


@router.message(StateFilter(None), ~F.text.startswith("/"))
async def handle_video_link(message: Message, state: FSMContext):
    """Handle video link messages"""
    # Extract URL from message
    url = extract_url(message.text or "")

    if not url:
        # Not a URL, ignore
        return

    if not is_video_url(url):
        await message.answer(
            format_error("invalid_link"),
            reply_markup=back_to_main_kb(),
            parse_mode="HTML",
        )
        return

    # Check subscription
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

    # Register user if not exists
    session_factory = await get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        await user_repo.get_or_create(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )

    # Start loading animation
    loading_msg = await message.answer("🕐 Link tekshirilmoqda...")

    # Animate loading
    animation_task = asyncio.create_task(
        _animate_loading(message.bot, loading_msg)
    )

    platform = detect_platform(url)

    try:
        # Extract video info
        result = await DownloadService.process_url(url)

        if result is None:
            animation_task.cancel()

            # YouTube uchun maxsus xato xabari
            if platform == "youtube":
                await loading_msg.edit_text(
                    "❌ <b>YouTube videosini yuklab bo'lmadi</b>\n\n"
                    "🔍 Sabab: Server IP manzili YouTube tomonidan bloklangan.\n\n"
                    "💡 <b>Yechimlar:</b>\n"
                    "1. Residential proxy qo'shing (YOUTUBE_PROXY env)\n"
                    "2. O'zingizning Cobalt serveringizni ishga tushiring\n"
                    "   → https://github.com/imputnet/cobalt\n"
                    "   → COBALT_API_URL va COBALT_API_KEY env o'rnating\n"
                    "3. Boshqa hosting xizmatiga o'ting\n\n"
                    "📱 Boshqa platformalar (TikTok, Instagram va h.k.) ishlaydi!",
                    reply_markup=back_to_main_kb(),
                    parse_mode="HTML",
                )
                return

            await loading_msg.edit_text(
                format_error("download_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
            return

        # Cancel animation
        animation_task.cancel()

        # Cache video info
        video_id = result["info"].get("id", str(hash(url)))
        _video_cache[video_id] = {
            "url": url,
            "info": result["info"],
            "platform": result["platform"],
        }

        # Clean cache if too large
        if len(_video_cache) > 100:
            oldest = list(_video_cache.keys())[:50]
            for k in oldest:
                del _video_cache[k]

        # Show video info with quality selection
        text = format_video_info(result["info"], result["platform"])
        kb = quality_select_kb(video_id, result["available_qualities"])

        # Try to send thumbnail
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
        logger.error(f"Error processing video: {e}")
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
    """Handle quality selection"""
    # Parse callback data: quality_{video_id}_{quality}
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("❌ Xatolik", show_alert=True)
        return

    video_id = parts[1]
    quality = parts[2]
    audio_only = quality == "mp3"

    # Get cached video info
    video_data = _video_cache.get(video_id)
    if not video_data:
        await callback.answer("⏰ Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    url = video_data["url"]

    # Start download with animation
    loading_msg = await callback.message.answer("🕐 Yuklab olinmoqda...")

    animation_task = asyncio.create_task(
        _animate_loading(callback.bot, loading_msg)
    )

    try:
        quality_str = quality if not audio_only else "720p"
        result = await DownloadService.download(
            url=url,
            quality=quality_str,
            audio_only=audio_only,
            user_id=callback.from_user.id,
        )

        animation_task.cancel()

        if result is None:
            # YouTube uchun maxsus xato xabari
            platform = video_data.get("platform", "")
            if platform == "youtube":
                await loading_msg.edit_text(
                    "❌ <b>YouTube videosini yuklab bo'lmadi</b>\n\n"
                    "Server IP bloklangan. Administrator bilan bog'laning.",
                    reply_markup=back_to_main_kb(),
                    parse_mode="HTML",
                )
                return

            await loading_msg.edit_text(
                format_error("download_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
            return

        file_path = result["file_path"]
        file_size_mb = result["file_size_mb"]

        # Send the file
        try:
            if audio_only:
                # Audio faylni yuborish
                await _send_audio(callback, file_path, result["info"])
            else:
                # Video faylni yuborish
                await _send_video(callback, file_path, result["info"], quality, file_size_mb)

            await loading_msg.delete()

        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await loading_msg.edit_text(
                format_error("server_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
        finally:
            # Cleanup file
            cleanup_file(file_path)

    except Exception as e:
        animation_task.cancel()
        logger.error(f"Error downloading: {e}")
        try:
            await loading_msg.edit_text(
                format_error("server_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
        except Exception:
            pass


async def _send_video(callback: CallbackQuery, file_path: str, info: dict,
                       quality: str, file_size_mb: float):
    """Videoni Telegramga yuborish - gallereyaga saqlash imkoni bilan.

    MUHIM: supports_streaming=True qo'shish kerak!
    Bu bo'lmasa, Telegram videoni "hujjat" sifatida ko'rsatadi
    va telefonga saqlab bo'lmaydi.
    """
    # Faylni mp4 formatiga keltirish
    file_path = _ensure_mp4(file_path)

    # Thumbnail URL
    thumbnail_url = info.get("thumbnail", "")

    caption = format_video_caption(info, quality.upper())

    if file_size_mb > config.download.max_file_size_mb:
        # Juda katta fayl - document sifatida yuborish
        await callback.message.answer_document(
            document=FSInputFile(file_path),
            caption=caption,
        )
        return

    # Video sifatida yuborish - supports_streaming MUHIM!
    try:
        video_file = FSInputFile(file_path)
        await callback.message.answer_video(
            video=video_file,
            caption=caption,
            supports_streaming=True,  # ← BU MUHIM! Gallereyaga saqlash uchun
        )
    except Exception as e:
        logger.warning(f"[Video] answer_video xatosi: {e}, document sifatida yuborilmoqda...")
        # Agar video sifatida yuborib bo'lmasa - document sifatida
        try:
            await callback.message.answer_document(
                document=FSInputFile(file_path),
                caption=caption,
            )
        except Exception as e2:
            logger.error(f"[Video] document sifatida ham yuborib bo'lmadi: {e2}")
            raise


async def _send_audio(callback: CallbackQuery, file_path: str, info: dict):
    """Audioni Telegramga yuborish."""
    # MP3 fayl nomini to'g'rilash
    title = info.get("title", "Audio")
    # Fayl nomida maxsus belgilarni olib tashlash
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:50]

    caption = f"🎵 {safe_title}\n🤖 Downloader Pro"

    await callback.message.answer_audio(
        audio=FSInputFile(file_path),
        caption=caption,
        title=safe_title,
    )


@router.callback_query(F.data == "cancel_download")
async def cancel_download(callback: CallbackQuery, state: FSMContext):
    """Cancel download"""
    await callback.message.edit_text(
        "❌ Yuklash bekor qilindi.",
        reply_markup=back_to_main_kb(),
    )


@router.callback_query(F.data == "download")
async def callback_download(callback: CallbackQuery):
    """Download button callback"""
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


async def _animate_loading(bot: Bot, message: Message):
    """Animate loading message with clock emojis"""
    step = 0
    try:
        while True:
            await asyncio.sleep(1)
            step += 1
            text = format_loading_step(step)
            try:
                await message.edit_text(text)
            except Exception:
                break
    except asyncio.CancelledError:
        pass
