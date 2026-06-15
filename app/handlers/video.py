"""Video Handler - Tez va sodda video yuklash

Foydalanuvchi link yuboradi → bot avtomatik yuklaydi →
video tagida MP3 tugmasi ko'rsatiladi.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Dict, Optional

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from app.config import config, SUPPORTED_PLATFORMS
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.download_repo import DownloadRepository
from app.services.subscription_service import SubscriptionService
from app.utils.downloader import (
    detect_platform, is_video_url,
    download_video, cleanup_file, LoginRequiredError,
)
from app.utils.helpers import extract_url_from_text as extract_url

logger = logging.getLogger(__name__)

router = Router()

# MP3 tugmasi uchun cache — video fayl yo'li va info saqlash
_url_cache: Dict[str, dict] = {}
_CACHE_TTL = 1800  # 30 daqiqa

# Yuklangan video fayl keshi — MP3 olish uchun
_video_file_cache: Dict[str, dict] = {}  # {key: {"file_path": str, "info": dict, "cached_at": float}}
_VIDEO_FILE_CACHE_TTL = 300  # 5 daqiqa

# Oldindan tayyorlangan MP3 fayl keshi
_mp3_ready_cache: Dict[str, str] = {}  # {key: mp3_file_path}

# Bot username
_BOT_LINK = "@UzVideoSaveBot"


def _ensure_mp4(file_path: str, force_reencode: bool = False) -> str:
    """Faylni Telegram uchun mos MP4 formatiga keltirish."""
    if not os.path.exists(file_path):
        return file_path

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".mp4" and not force_reencode:
        # MP4 fayl allaqachon mos — tezkor kodek tekshirish
        if config.download.ffmpeg_available:
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_streams", file_path],
                    capture_output=True, timeout=5
                )
                if probe.returncode == 0:
                    import json
                    streams = json.loads(probe.stdout).get("streams", [])
                    vcodec = next((s.get("codec_name", "") for s in streams if s.get("codec_type") == "video"), "")
                    acodec = next((s.get("codec_name", "") for s in streams if s.get("codec_type") == "audio"), "")
                    # H.264 + AAC = Telegram uchun tayyor, qayta kodlash shart emas
                    if vcodec == "h264" and acodec in ("aac", "mp4a"):
                        logger.info("[Video] H.264+AAC — qayta kodlash shart emas")
                        return file_path
            except Exception:
                pass  # ffprobe ishlamasa, faylni qaytarib yuboramiz
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

        logger.info(f"[Video] {'Qayta kodlash' if ext == '.mp4' else f'{ext} → mp4'}")

        result = subprocess.run(
            ["ffmpeg", "-i", file_path,
             "-c:v", "libx264",
             "-c:a", "aac",
             "-movflags", "+faststart",
             "-preset", "ultrafast",
             "-crf", "28",
             "-pix_fmt", "yuv420p",
             "-threads", "2",
             "-y", new_path],
            capture_output=True, timeout=45
        )

        if result.returncode == 0 and os.path.exists(new_path) and os.path.getsize(new_path) > 0:
            try:
                if file_path != new_path:
                    os.remove(file_path)
            except OSError:
                pass
            return new_path
        else:
            if os.path.exists(new_path) and file_path != new_path:
                try:
                    os.remove(new_path)
                except OSError:
                    pass
            if ext != ".mp4":
                renamed = file_path.rsplit(".", 1)[0] + ".mp4"
                if not os.path.exists(renamed):
                    os.rename(file_path, renamed)
                    return renamed
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


def _make_mp3_kb(url: str) -> InlineKeyboardMarkup:
    """Video tagidagi MP3 tugmasi."""
    key = str(hash(url) % 100000000)
    _url_cache[key] = {"url": url, "cached_at": time.time()}

    # Eski cache tozalash
    now = time.time()
    expired = [k for k, v in _url_cache.items() if now - v.get("cached_at", 0) > _CACHE_TTL]
    for k in expired:
        del _url_cache[k]
    # Video fayl kesh tozalash
    vf_expired = [k for k, v in _video_file_cache.items() if now - v.get("cached_at", 0) > _VIDEO_FILE_CACHE_TTL]
    for k in vf_expired:
        old_path = _video_file_cache[k].get("file_path", "")
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
        del _video_file_cache[k]
    # MP3 tayyor kesh tozalash
    mp3_expired_keys = [k for k, v in _url_cache.items() if now - v.get("cached_at", 0) > _CACHE_TTL]
    for k in list(_mp3_ready_cache.keys()):
        if k not in _url_cache:
            old_mp3 = _mp3_ready_cache.pop(k, "")
            if old_mp3 and os.path.exists(old_mp3):
                try:
                    os.remove(old_mp3)
                except OSError:
                    pass

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 MP3", callback_data=f"mp3_{key}")]
    ])


def _format_story_error(missing_cookies: list = None) -> str:
    """Instagram story xato xabari."""
    base = "❌ <b>Instagram Story yuklab bo'lmadi</b>\n\n"
    if missing_cookies:
        missing_str = ", ".join(f"<code>{c}</code>" for c in missing_cookies)
        base += f"🔍 Cookie'lar yetishmayapti: {missing_str}\n\n"
    else:
        base += "🔍 Story uchun Instagram akkaunti kerak.\n\n"
    base += (
        "💡 Cookie faylini yangilash uchun administratorga murojaat qiling.\n\n"
        "📸 Reels va Post'lar ishlaydi!"
    )
    return base


def _extract_mp3_sync(video_path: str, mp3_path: str) -> bool:
    """ffmpeg bilan MP3 ajratish (sync — thread da ishlaydi)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vn",
             "-acodec", "libmp3lame",
             "-ab", "128k",
             "-ar", "44100",
             "-ac", "2",
             "-y", mp3_path],
            capture_output=True, timeout=20
        )
        return result.returncode == 0 and os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0
    except Exception as e:
        logger.warning(f"[MP3] ffmpeg xatosi: {e}")
        return False


async def _pre_extract_mp3(key: str, video_path: str, info: dict):
    """Fon rejimida MP3 tayyorlash — foydalanuvchi MP3 tugmasini bosganda tayyor bo'ladi."""
    if not config.download.ffmpeg_available:
        return

    mp3_dir = os.path.join(tempfile.gettempdir(), "mp3_cache")
    os.makedirs(mp3_dir, exist_ok=True)
    mp3_path = os.path.join(mp3_dir, f"{key}_pre.mp3")

    try:
        success = await asyncio.to_thread(_extract_mp3_sync, video_path, mp3_path)
        if success:
            _mp3_ready_cache[key] = mp3_path
            logger.info(f"[MP3] Oldindan tayyorlandi: {mp3_path}")
        else:
            logger.warning("[MP3] Oldindan tayyorlash muvaffaqiyatsiz")
    except Exception as e:
        logger.warning(f"[MP3] Oldindan tayyorlash xatosi: {e}")


@router.message(StateFilter(None), ~F.text.startswith("/"))
async def handle_video_link(message: Message, state: FSMContext):
    """Link → avtomatik yuklash → video + MP3 tugmasi"""
    url = extract_url(message.text or "")
    if not url:
        return

    if not is_video_url(url):
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

    # ⏳ Loading
    loading_msg = await message.answer("⏳")

    try:
        # TO'G'RIDAN-TO'G'RI YUKLASH — info olmay, faqat yuklaydi
        result = await download_video(url, "720", audio_only=False)

        if result is None:
            try:
                await loading_msg.delete()
            except Exception:
                pass

            if platform == "youtube":
                await message.answer(
                    "❌ <b>YouTube yuklab bo'lmadi</b>\n\n"
                    "🔍 Sabab: YouTube server IP ni bloklamoqda (bot detektsiya).\n\n"
                    "💡 Yechim:\n"
                    "• Keyinroq qayta urinib ko'ring\n"
                    "• Boshqa video linkini yuboring\n"
                    "• Agar doim shu xato chiqsa — administratorga xabar bering",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    "❌ <b>Video yuklab bo'lmadi.</b>\n\nLinkni tekshiring va qayta urinib ko'ring.",
                    parse_mode="HTML",
                )
            return

    except LoginRequiredError as e:
        try:
            await loading_msg.delete()
        except Exception:
            pass

        if e.platform == "instagram" and e.content_type == "story":
            await message.answer(_format_story_error(e.missing_cookies), parse_mode="HTML")
        else:
            await message.answer(
                "❌ <b>Video yuklab bo'lmadi.</b>\n\nLinkni tekshiring va qayta urinib ko'ring.",
                parse_mode="HTML",
            )
        return

    except Exception as e:
        logger.error(f"Download error: {e}")
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer("⚠️ <b>Server band.</b> Qayta urinib ko'ring.", parse_mode="HTML")
        return

    file_path, info = result
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

    # Instagram uchun qayta kodlash
    extractor = info.get("extractor", "") if info else ""
    force_reencode = extractor == "instagram" or "/stories/" in str(info.get("webpage_url", "") if info else "")
    file_path = _ensure_mp4(file_path, force_reencode=force_reencode)

    # Video faylni MP3 olish uchun keshlash (5 daqiqa)
    mp3_key = str(hash(url) % 100000000)
    cache_dir = os.path.join(tempfile.gettempdir(), "mp3_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cached_file = os.path.join(cache_dir, f"{mp3_key}.mp4")
    try:
        import shutil
        shutil.copy2(file_path, cached_file)
        _video_file_cache[mp3_key] = {
            "file_path": cached_file,
            "info": info or {},
            "cached_at": time.time(),
        }
        logger.info(f"[MP3] Video fayl keshlandi: {cached_file} ({file_size_mb:.1f}MB)")
    except Exception as e:
        logger.warning(f"[MP3] Fayl keshlash xatosi: {e}")

    # Caption — faqat bot linki
    caption = f"🤖 {_BOT_LINK}"

    # MP3 tugmasi
    mp3_kb = _make_mp3_kb(url)

    # Sticker o'chirish
    try:
        await loading_msg.delete()
    except Exception:
        pass

    # Video yuborish
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
        logger.error(f"Send error: {e}")
        await message.answer("⚠️ <b>Video yuborishda xatolik.</b>", parse_mode="HTML")
    finally:
        cleanup_file(file_path)

    # Fon rejimida MP3 tayyorlash — foydalanuvchi tugmasini bosganda tayyor bo'ladi
    if cached_file and os.path.exists(cached_file):
        asyncio.create_task(_pre_extract_mp3(mp3_key, cached_file, info or {}))

    # Bazaga yozish
    try:
        session_factory = await get_session_factory()
        async with session_factory() as session:
            download_repo = DownloadRepository(session)
            user_repo = UserRepository(session)
            await download_repo.create(
                user_id=message.from_user.id,
                platform=platform or "unknown",
                url=url,
                quality="720p",
                file_size=file_size_mb,
            )
            await user_repo.update_download_count(message.from_user.id)
    except Exception as e:
        logger.error(f"DB error: {e}")


@router.callback_query(F.data.startswith("mp3_"))
async def handle_mp3_request(callback: CallbackQuery, state: FSMContext):
    """MP3 tugmasi → audio yuklash (oldindan tayyorlangan yoki keshlangan fayldan)"""
    key = callback.data.replace("mp3_", "")
    cache_data = _url_cache.get(key)

    if not cache_data or "url" not in cache_data:
        await callback.answer("⏰ Qayta link yuboring.", show_alert=True)
        return

    if time.time() - cache_data.get("cached_at", 0) > _CACHE_TTL:
        del _url_cache[key]
        await callback.answer("⏰ Qayta link yuboring.", show_alert=True)
        return

    # Callback javobini ZUDLIK BILAN berish — Telegram 30s dan keyin o'chiradi
    await callback.answer("⏬ Audio yuklanmoqda...")

    url = cache_data["url"]
    file_path_to_cleanup = None

    # 1-USUL: Oldindan tayyorlangan MP3 fayl (eng tez — 0 soniya kutish)
    pre_mp3 = _mp3_ready_cache.get(key)
    if pre_mp3 and os.path.exists(pre_mp3):
        logger.info(f"[MP3] Oldindan tayyorlangan fayl topildi: {pre_mp3}")
        cached_video = _video_file_cache.get(key)
        info = cached_video.get("info", {}) if cached_video else {}
        title = info.get("title", "Audio")
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:50]
        caption = f"🤖 {_BOT_LINK}"

        try:
            await callback.message.answer_audio(
                audio=FSInputFile(pre_mp3),
                caption=caption,
                title=safe_title,
            )
        except Exception:
            await callback.message.answer_document(
                document=FSInputFile(pre_mp3),
                caption=caption,
            )
        logger.info("[MP3] Oldindan tayyorlangan fayl muvaffaqiyatli yuborildi!")
        return

    # 2-USUL: Keshlangan video fayldan ffmpeg bilan audio ajratish (1-3 soniya)
    cached_video = _video_file_cache.get(key)
    if cached_video and os.path.exists(cached_video.get("file_path", "")):
        video_path = cached_video["file_path"]
        info = cached_video.get("info", {})
        logger.info(f"[MP3] Keshlangan fayldan audio ajratilmoqda: {video_path}")

        if config.download.ffmpeg_available:
            try:
                mp3_dir = tempfile.mkdtemp()
                mp3_path = os.path.join(mp3_dir, f"{key}.mp3")

                # asyncio.to_thread — event loop ni bloklamaydi
                success = await asyncio.to_thread(_extract_mp3_sync, video_path, mp3_path)

                if success:
                    file_path_to_cleanup = mp3_path
                    title = info.get("title", "Audio") if info else "Audio"
                    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:50]
                    caption = f"🤖 {_BOT_LINK}"

                    try:
                        await callback.message.answer_audio(
                            audio=FSInputFile(mp3_path),
                            caption=caption,
                            title=safe_title,
                        )
                    except Exception:
                        await callback.message.answer_document(
                            document=FSInputFile(mp3_path),
                            caption=caption,
                        )

                    logger.info("[MP3] Keshlangan fayldan muvaffaqiyatli ajratildi!")
                    return
                else:
                    logger.warning("[MP3] ffmpeg audio ajratish xatosi")
            except Exception as e:
                logger.warning(f"[MP3] ffmpeg xatosi: {e}")

    # 3-USUL: Qayta yuklash (sekin, lekin ishonchli)
    loading_msg = await callback.message.answer("⏳")
    try:
        result = await download_video(url, "720", audio_only=True)

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if result is None:
            await callback.message.answer("❌ <b>Audio yuklab bo'lmadi.</b>", parse_mode="HTML")
            return

        file_path, info = result
        file_path_to_cleanup = file_path

        title = info.get("title", "Audio") if info else "Audio"
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:50]
        caption = f"🤖 {_BOT_LINK}"

        try:
            await callback.message.answer_audio(
                audio=FSInputFile(file_path),
                caption=caption,
                title=safe_title,
            )
        except Exception:
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
            await callback.message.answer(_format_story_error(e.missing_cookies), parse_mode="HTML")
        else:
            await callback.message.answer("❌ <b>Audio yuklab bo'lmadi.</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"MP3 error: {e}")
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await callback.message.answer("⚠️ <b>Server band.</b>", parse_mode="HTML")
    finally:
        if file_path_to_cleanup:
            cleanup_file(file_path_to_cleanup)


@router.callback_query(F.data == "cancel_download")
async def cancel_download(callback: CallbackQuery, state: FSMContext):
    """Bekor qilish"""
    try:
        await callback.message.edit_text("❌ Bekor qilindi.")
    except Exception:
        try:
            await callback.answer("❌ Bekor qilindi")
        except Exception:
            pass