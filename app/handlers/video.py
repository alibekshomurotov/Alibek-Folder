import asyncio
import logging
import os
import subprocess
import tempfile
import time
from typing import Dict, Optional

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from app.config import config, SUPPORTED_PLATFORMS
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.download_repo import DownloadRepository
from app.services.subscription_service import SubscriptionService
from app.utils.downloader import (
    detect_platform, is_video_url,
    download_video_auto_quality, download_video,
    cleanup_file, LoginRequiredError,
)
from app.utils.helpers import extract_url_from_text as extract_url

logger = logging.getLogger(__name__)

router = Router()

# Video cache faqat MP3 tugmasi uchun (url → file info)
_url_cache: Dict[str, dict] = {}
_CACHE_TTL = 1800  # 30 daqiqa

# Loading sticker ID lari
_LOADING_STICKERS = [
    "💡",  # fallback — agar custom sticker bo'lmasa
]


def _get_platform_emoji(platform: str) -> str:
    """Platforma emoji sini olish."""
    emojis = {
        "tiktok": "🎵",
        "instagram": "📸",
        "youtube": "▶️",
        "facebook": "📘",
        "twitter": "🐦",
        "pinterest": "📌",
        "snapchat": "👻",
        "threads": "🧵",
    }
    return emojis.get(platform, "🎬")


def _get_platform_name(platform: str) -> str:
    """Platforma nomini olish."""
    names = {
        "tiktok": "TikTok",
        "instagram": "Instagram",
        "youtube": "YouTube",
        "facebook": "Facebook",
        "twitter": "X (Twitter)",
        "pinterest": "Pinterest",
        "snapchat": "Snapchat",
        "threads": "Threads",
    }
    return names.get(platform, "Video")


def _ensure_mp4(file_path: str, force_reencode: bool = False) -> str:
    """Faylni Telegram uchun mos MP4 formatiga keltirish."""
    if not os.path.exists(file_path):
        return file_path

    ext = os.path.splitext(file_path)[1].lower()

    # .mp4 bo'lsa VA force_reencode yo'q bo'lsa — o'zgartirmasdan qaytaramiz
    if ext == ".mp4" and not force_reencode:
        return file_path

    if not config.download.ffmpeg_available:
        if ext != ".mp4":
            new_path = file_path.rsplit(".", 1)[0] + ".mp4"
            os.rename(file_path, new_path)
            return new_path
        return file_path

    try:
        new_path = file_path.rsplit(".", 1)[0] + ".mp4"
        if file_path == new_path:
            new_path = file_path.rsplit(".", 1)[0] + "_telegram.mp4"

        action = "qayta kodlash" if ext == ".mp4" else f"konvertatsiya: {ext} → mp4"
        logger.info(f"[Video] {action}")

        result = subprocess.run(
            ["ffmpeg", "-i", file_path,
             "-c:v", "libx264",
             "-c:a", "aac",
             "-movflags", "+faststart",
             "-preset", "fast",
             "-crf", "28",
             "-pix_fmt", "yuv420p",
             "-y", new_path],
            capture_output=True, timeout=90
        )

        if result.returncode == 0 and os.path.exists(new_path) and os.path.getsize(new_path) > 0:
            try:
                if file_path != new_path:
                    os.remove(file_path)
            except OSError:
                pass
            logger.info(f"[Video] {action} muvaffaqiyatli")
            return new_path
        else:
            stderr_output = result.stderr.decode('utf-8', errors='ignore')[-200:] if result.stderr else ""
            logger.warning(f"[Video] Konvertatsiya xatosi: {stderr_output}")
            if os.path.exists(new_path) and file_path != new_path:
                try:
                    os.remove(new_path)
                except OSError:
                    pass
            if ext != ".mp4":
                renamed_path = file_path.rsplit(".", 1)[0] + ".mp4"
                if not os.path.exists(renamed_path):
                    os.rename(file_path, renamed_path)
                    return renamed_path
            return file_path

    except subprocess.TimeoutExpired:
        logger.warning("[Video] Konvertatsiya timeout")
        if ext != ".mp4":
            new_path = file_path.rsplit(".", 1)[0] + ".mp4"
            if not os.path.exists(new_path):
                os.rename(file_path, new_path)
                return new_path
        return file_path
    except Exception as e:
        logger.warning(f"[Video] Konvertatsiya xatosi: {e}")
        if ext != ".mp4":
            new_path = file_path.rsplit(".", 1)[0] + ".mp4"
            if not os.path.exists(new_path):
                os.rename(file_path, new_path)
                return new_path
        return file_path


def _make_video_caption(info: dict, platform: str) -> str:
    """Video tagiga sodda caption yaratish.

    Faqat: platforma emoji + nomi, bot linki
    """
    emoji = _get_platform_emoji(platform)
    platform_name = _get_platform_name(platform)
    bot_username = config.bot.username
    bot_link = f"https://t.me/{bot_username}" if bot_username else "@downloader_pro"

    uploader = ""
    if info:
        uploader = info.get("uploader", "") or info.get("channel", "") or info.get("uploader_id", "")
        if uploader:
            uploader = f"\n👤 {uploader}"

    return f"{emoji} {platform_name}{uploader}\n🤖 {bot_link}"


def _make_mp3_kb(url: str) -> "InlineKeyboardMarkup":
    """Video tagidagi MP3 tugmasi."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # URL ni cache kalit sifatida ishlatamiz
    _url_cache[url] = {"cached_at": time.time()}

    # Eski cache larni tozalash
    now = time.time()
    expired = [k for k, v in _url_cache.items() if now - v.get("cached_at", 0) > _CACHE_TTL]
    for k in expired:
        del _url_cache[k]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 MP3 olish", callback_data=f"mp3_{hash(url) % 100000000}")]
    ])
    # Haqiqiy URL ni ham saqlaymiz
    _url_cache[f"mp3_{hash(url) % 100000000}"] = {"url": url, "cached_at": time.time()}

    return kb


def _format_story_error(missing_cookies: list = None) -> str:
    """Instagram story xato xabari."""
    base = "❌ <b>Instagram Story yuklab bo'lmadi</b>\n\n"

    if missing_cookies:
        missing_str = ", ".join(f"<code>{c}</code>" for c in missing_cookies)
        base += (
            f"🔍 <b>Sabab:</b> Cookie faylda quyidagi cookie'lar yetishmayapti:\n"
            f"{missing_str}\n\n"
        )
    else:
        base += "🔍 <b>Sabab:</b> Story'larni ko'rish uchun Instagram akkaunti kerak.\n\n"

    base += (
        "💡 <b>Yechim:</b>\n"
        "1. Chrome brauzerida Instagram'ga kiring\n"
        "2. \"Get cookies.txt LOCALLY\" kengaytmasini o'rnating\n"
        "3. Instagram sahifasida kengaytmani bosing → Export\n"
        "4. Cookie faylini administratorga yuboring\n\n"
        "⚠️ <b>Muhim:</b>\n"
        "• <code>sessionid</code>, <code>ds_user_id</code>, <code>csrftoken</code> — KRITIK\n"
        "• Cookie'lar har 1-2 haftada yangilanishi kerak\n\n"
        "📸 Reels va Post'lar ishlaydi!"
    )
    return base


@router.message(StateFilter(None), ~F.text.startswith("/"))
async def handle_video_link(message: Message, state: FSMContext):
    """Link yuborilganda → avtomatik eng yuqori sifatda yuklash"""
    url = extract_url(message.text or "")

    if not url:
        return

    if not is_video_url(url):
        await message.answer(
            "❌ <b>Link aniqlanmadi.</b>\n\n"
            "Qo'llab-quvvatlanadigan platformalar:\n"
            "TikTok, Instagram, YouTube, Facebook, X, Pinterest, Snapchat, Threads",
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

    # Foydalanuvchini ro'yxatga olish
    session_factory = await get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        await user_repo.get_or_create(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )

    platform = detect_platform(url)
    platform_emoji = _get_platform_emoji(platform or "")

    # ⏳ Loading sticker yuborish
    loading_msg = await message.answer("⏳")

    try:
        # Avtomatik eng yuqori sifatda yuklash
        result = await download_video_auto_quality(url, "1080", audio_only=False)

        if result is None:
            # Sticker o'chirish
            try:
                await loading_msg.delete()
            except Exception:
                pass

            # YouTube maxsus xato
            if platform == "youtube":
                await message.answer(
                    "❌ <b>YouTube videosini yuklab bo'lmadi</b>\n\n"
                    "Server IP bloklangan. Administrator bilan bog'laning.",
                    parse_mode="HTML",
                )
                return

            await message.answer(
                "❌ <b>Video yuklab bo'lmadi.</b>\n\n"
                "Link to'g'ri ekanligini tekshiring va qayta urinib ko'ring.",
                parse_mode="HTML",
            )
            return

    except LoginRequiredError as e:
        try:
            await loading_msg.delete()
        except Exception:
            pass

        if e.platform == "instagram" and e.content_type == "story":
            await message.answer(
                _format_story_error(e.missing_cookies),
                parse_mode="HTML",
            )
            return

        await message.answer(
            "❌ <b>Video yuklab bo'lmadi.</b>\n\n"
            "Link to'g'ri ekanligini tekshiring va qayta urinib ko'ring.",
            parse_mode="HTML",
        )
        return

    except Exception as e:
        logger.error(f"Error downloading: {e}")
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer(
            "⚠️ <b>Server vaqtincha band.</b>\n\nIltimos, qayta urinib ko'ring.",
            parse_mode="HTML",
        )
        return

    file_path, info = result
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

    # Instagram va story videolar uchun qayta kodlash
    extractor = info.get("extractor", "") if info else ""
    force_reencode = extractor in ("instagram",) or "/stories/" in str(info.get("webpage_url", "") if info else "")
    file_path = _ensure_mp4(file_path, force_reencode=force_reencode)

    # Caption
    caption = _make_video_caption(info, platform or "")
    # MP3 tugmasi
    mp3_kb = _make_mp3_kb(url)

    # Sticker o'chirish va video yuborish
    try:
        await loading_msg.delete()
    except Exception:
        pass

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
                    supports_streaming=True,
                    reply_markup=mp3_kb,
                )
            except Exception:
                await message.answer_document(
                    document=FSInputFile(file_path),
                    caption=caption,
                    reply_markup=mp3_kb,
                )
    except Exception as e:
        logger.error(f"Error sending video: {e}")
        await message.answer(
            "⚠️ <b>Video yuborishda xatolik.</b>",
            parse_mode="HTML",
        )
    finally:
        cleanup_file(file_path)

    # Yuklashni bazaga yozish
    try:
        session_factory = await get_session_factory()
        async with session_factory() as session:
            download_repo = DownloadRepository(session)
            user_repo = UserRepository(session)
            await download_repo.create(
                user_id=message.from_user.id,
                platform=platform or "unknown",
                url=url,
                quality="1080p",
                file_size=file_size_mb,
            )
            await user_repo.update_download_count(message.from_user.id)
    except Exception as e:
        logger.error(f"Failed to record download: {e}")


@router.callback_query(F.data.startswith("mp3_"))
async def handle_mp3_request(callback: CallbackQuery, state: FSMContext):
    """MP3 tugmasi bosilganda audioni yuklash"""
    callback_data = callback.data

    # Cachedan URL ni olish
    cache_data = _url_cache.get(callback_data)
    if not cache_data or "url" not in cache_data:
        await callback.answer("⏰ Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    url = cache_data["url"]

    # TTL tekshirish
    if time.time() - cache_data.get("cached_at", 0) > _CACHE_TTL:
        del _url_cache[callback_data]
        await callback.answer("⏰ Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    platform = detect_platform(url) or "unknown"

    # Loading
    loading_msg = await callback.message.answer("⏳")

    file_path_to_cleanup = None
    try:
        result = await download_video(url, "720", audio_only=True)

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if result is None:
            await callback.message.answer(
                "❌ <b>Audio yuklab bo'lmadi.</b>",
                parse_mode="HTML",
            )
            return

        file_path, info = result
        file_path_to_cleanup = file_path

        # Audio fayl nomi
        title = info.get("title", "Audio") if info else "Audio"
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:50]
        platform_emoji = _get_platform_emoji(platform)
        caption = f"🎵 {platform_emoji} {_get_platform_name(platform)}\n🤖 {f'https://t.me/{config.bot.username}' if config.bot.username else '@downloader_pro'}"

        try:
            await callback.message.answer_audio(
                audio=FSInputFile(file_path),
                caption=caption,
                title=safe_title,
            )
        except Exception as e:
            logger.warning(f"[MP3] answer_audio xatosi: {e}, document sifatida yuborilmoqda")
            await callback.message.answer_document(
                document=FSInputFile(file_path),
                caption=caption,
            )

    except LoginRequiredError as e:
        try:
            await loading_msg.delete()
        except Exception:
            pass
        if e.platform == "instagram" and e.content_type == "story":
            await callback.message.answer(
                _format_story_error(e.missing_cookies),
                parse_mode="HTML",
            )
        else:
            await callback.message.answer(
                "❌ <b>Audio yuklab bo'lmadi.</b>",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Error downloading MP3: {e}")
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await callback.message.answer(
            "⚠️ <b>Server vaqtincha band.</b>",
            parse_mode="HTML",
        )
    finally:
        if file_path_to_cleanup:
            cleanup_file(file_path_to_cleanup)

    await callback.answer()


@router.callback_query(F.data == "cancel_download")
async def cancel_download(callback: CallbackQuery, state: FSMContext):
    """Bekor qilish"""
    try:
        await callback.message.edit_text("❌ Yuklash bekor qilindi.")
    except Exception:
        try:
            await callback.answer("❌ Bekor qilindi")
        except Exception:
            pass
