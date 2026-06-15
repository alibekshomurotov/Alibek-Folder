import asyncio
import logging
import os
import subprocess
import tempfile
import time
from typing import Optional, Dict

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

try:
    from shazamio import Shazam
except ImportError:
    Shazam = None  # type: ignore[assignment, misc]

from app.config import config
from app.services.subscription_service import SubscriptionService
from app.utils.downloader import download_video, cleanup_file

logger = logging.getLogger(__name__)

router = Router()

# Qo'shiq ma'lumotlari keshi — download tugmasi uchun
_music_cache: Dict[str, dict] = {}
_MUSIC_CACHE_TTL = 1800  # 30 daqiqa


# ============ Shazam yordamchi funksiyalar ============

async def recognize_audio(file_path: str) -> dict | None:
    """Shazamio orqali audioni tanish"""
    if Shazam is None:
        logger.error("[Music] shazamio kutubxonasi o'rnatilmagan")
        return None
    try:
        shazam = Shazam()
        result = await shazam.recognize_song(file_path)
        return result
    except Exception as e:
        logger.error(f"[Music] Shazam tanish xatosi: {e}")
        return None


async def search_song(query: str, limit: int = 5) -> list:
    """Shazam Search orqali qo'shiq nomi bo'yicha qidirish"""
    if Shazam is None:
        logger.error("[Music] shazamio kutubxonasi o'rnatilmagan")
        return []
    try:
        shazam = Shazam()
        results = await shazam.search_track(query=query, limit=limit)
        tracks = results.get("tracks", {}).get("hits", [])
        return tracks
    except Exception as e:
        logger.error(f"[Music] Shazam search xatosi: {e}")
        return []


def _extract_youtube_url(result: dict) -> str:
    """Shazam natijasidan YouTube linkini olish"""
    track = result.get("track", {})
    hub = track.get("hub", {})
    providers = hub.get("providers", [])

    for provider in providers:
        if provider.get("type") == "YOUTUBE":
            actions = provider.get("actions", [])
            for action in actions:
                uri = action.get("uri", "")
                if "youtube.com" in uri or "youtu.be" in uri:
                    return uri

    return ""


def _extract_track_info(result: dict) -> dict:
    """Shazam natijasidan qo'shiq ma'lumotlarini olish"""
    track = result.get("track", {})
    title = track.get("title", "Noma'lum")
    subtitle = track.get("subtitle", "")
    yt_url = _extract_youtube_url(result)

    info = {
        "title": title,
        "artist": subtitle,
        "youtube_url": yt_url,
    }

    # Album va metadata
    sections = track.get("sections", [])
    for section in sections:
        if section.get("type") == "SONG":
            metadata = section.get("metadata", [])
            for meta in metadata:
                mtitle = meta.get("title", "")
                mtext = meta.get("text", "")
                if mtitle and mtext:
                    info[mtitle.lower()] = mtext

    # Cover art
    images = track.get("images", {})
    coverart = images.get("coverart", "")
    if coverart:
        info["coverart"] = coverart

    return info


def format_music_result(result: dict) -> str | None:
    """Shazam audio tanish natijasini formatlash"""
    track = result.get("track")
    if not track:
        return None

    title = track.get("title", "Noma'lum")
    subtitle = track.get("subtitle", "")

    text = (
        f"🎵 <b>Qo'shiq topildi!</b>\n\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
        f"🎤 <b>Ijrochi:</b> {subtitle}\n"
        f"🎶 <b>Qo'shiq:</b> {title}\n"
    )

    # Album va metadata
    sections = track.get("sections", [])
    for section in sections:
        if section.get("type") == "SONG":
            metadata = section.get("metadata", [])
            for meta in metadata:
                mtitle = meta.get("title", "")
                mtext = meta.get("text", "")
                if mtitle and mtext:
                    emoji_map = {
                        "Album": "💿",
                        "Label": "🏷",
                        "Released": "📅",
                        "Genre": "🎭",
                        "Key": "🎹",
                        "Bpm": "🥁",
                    }
                    emoji = emoji_map.get(mtitle, "📌")
                    text += f"{emoji} <b>{mtitle}:</b> {mtext}\n"

    # YouTube link
    hub = track.get("hub", {})
    providers = hub.get("providers", [])
    yt_link = ""
    for provider in providers:
        if provider.get("type") == "YOUTUBE":
            actions = provider.get("actions", [])
            for action in actions:
                uri = action.get("uri", "")
                if "youtube.com" in uri or "youtu.be" in uri:
                    yt_link = uri
                    break
        if yt_link:
            break

    # Streaming links
    streaming_text = ""
    if yt_link:
        streaming_text += f"▶️ <b>YouTube:</b> {yt_link}\n"

    # Apple Music link
    if hub.get("type") == "MUSIC":
        options = hub.get("options", [])
        for opt in options:
            if opt.get("provider") == "apple":
                apple_link = opt.get("actions", [{}])[0].get("uri", "")
                if apple_link:
                    streaming_text += f"🍎 <b>Apple Music:</b> {apple_link}\n"

    # Shazam link
    share = track.get("share", {})
    if isinstance(share, dict):
        share_href = share.get("href", "")
        if share_href:
            streaming_text += f"🔗 <b>Shazam:</b> {share_href}\n"

    if streaming_text:
        text += f"\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n🎵 <b>Eshitish:</b>\n{streaming_text}"

    return text


def format_search_results(query: str, tracks: list) -> str:
    """Qidiruv natijalarini formatlash"""
    if not tracks:
        return (
            f"🔍 <b>Qidiruv:</b> {query}\n\n"
            f"❌ <b>Hech narsa topilmadi.</b>\n\n"
            f"💡 Boshqa nom bilan qidirib ko'ring."
        )

    text = (
        f"🔍 <b>Qidiruv:</b> {query}\n\n"
        f"🎵 <b>Natijalar:</b>\n\n"
    )

    for i, hit in enumerate(tracks[:5], 1):
        track = hit.get("track", hit)
        title = track.get("title", "Noma'lum")
        subtitle = track.get("subtitle", "")

        text += f"<b>{i}.</b> 🎤 {subtitle} — 🎶 {title}\n"

        # Album / Released
        sections = track.get("sections", [])
        for section in sections:
            if section.get("type") == "SONG":
                metadata = section.get("metadata", [])
                for meta in metadata:
                    mtitle = meta.get("title", "")
                    mtext = meta.get("text", "")
                    if mtitle in ("Album", "Released") and mtext:
                        emoji = "💿" if mtitle == "Album" else "📅"
                        text += f"    {emoji} {mtext}\n"

        # YouTube link
        hub = track.get("hub", {})
        providers = hub.get("providers", [])
        for provider in providers:
            if provider.get("type") == "YOUTUBE":
                actions = provider.get("actions", [])
                for action in actions:
                    uri = action.get("uri", "")
                    if "youtube.com" in uri or "youtu.be" in uri:
                        text += f"    ▶️ {uri}\n"
                        break
                break

        text += "\n"

    return text


# ============ Kesh boshqaruvi ============

def _cache_music(key: str, track_info: dict, yt_url: str):
    """Qo'shiq ma'lumotlarini keshlash — download tugmasi uchun"""
    _music_cache[key] = {
        "track_info": track_info,
        "yt_url": yt_url,
        "cached_at": time.time(),
    }

    # Eski kesh tozalash
    now = time.time()
    expired = [k for k, v in _music_cache.items() if now - v.get("cached_at", 0) > _MUSIC_CACHE_TTL]
    for k in expired:
        del _music_cache[k]


def _make_download_kb(key: str) -> InlineKeyboardMarkup:
    """Qo'shiq ma'lumotlari ostidagi 'Yuklash' tugmasi"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Qo'shiqni yuklash", callback_data=f"music_dl_{key}")]
    ])


# ============ Audio yuklab yuborish ============

async def _download_and_send_audio(
    message_or_callback,
    yt_url: str,
    track_info: dict,
    is_callback: bool = False,
) -> bool:
    """YouTube dan audio yuklab, foydalanuvchiga yuborish.

    Returns:
        True — muvaffaqiyatli yuborildi
        False — xatolik yuz berdi
    """
    if not yt_url:
        return False

    # Callback yoki Message dan foydalanish
    if is_callback:
        msg = message_or_callback.message
    else:
        msg = message_or_callback

    title = track_info.get("title", "Audio")
    artist = track_info.get("artist", "")
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:50]
    safe_artist = "".join(c for c in artist if c.isalnum() or c in " -_").strip()[:40]
    performer = safe_artist if safe_artist else ""

    loading_text = await msg.answer("⏳ <b>Qo'shiq yuklanmoqda...</b>", parse_mode="HTML")

    file_path_to_cleanup = None

    try:
        # YouTube dan audio yuklash
        result = await download_video(yt_url, "720", audio_only=True)

        try:
            await loading_text.delete()
        except Exception:
            pass

        if result is None:
            logger.warning(f"[Music] Audio yuklab bo'lmadi: {yt_url}")
            return False

        file_path, info = result
        file_path_to_cleanup = file_path
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        # Agar fayl .mp3 bo'lmasa va ffmpeg mavjud bo'lsa — MP3 ga konvertatsiya
        if not file_path.endswith(".mp3") and config.download.ffmpeg_available:
            mp3_path = file_path.rsplit(".", 1)[0] + ".mp3"
            try:
                conv_result = subprocess.run(
                    ["ffmpeg", "-i", file_path,
                     "-vn",
                     "-acodec", "libmp3lame",
                     "-ab", "192k",
                     "-ar", "44100",
                     "-ac", "2",
                     "-y", mp3_path],
                    capture_output=True, timeout=30
                )
                if conv_result.returncode == 0 and os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    file_path = mp3_path
                    file_path_to_cleanup = mp3_path
                    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    logger.info(f"[Music] MP3 konvertatsiya: {mp3_path} ({file_size_mb:.1f}MB)")
            except Exception as e:
                logger.warning(f"[Music] MP3 konvertatsiya xatosi: {e}")

        # Audio faylni yuborish
        caption = f"🎤 {performer}\n🎶 {safe_title}" if performer else f"🎶 {safe_title}"

        # Telegram 50MB limit
        if file_size_mb > 50:
            await msg.answer_document(
                document=FSInputFile(file_path),
                caption=caption,
            )
        else:
            try:
                await msg.answer_audio(
                    audio=FSInputFile(file_path),
                    caption=caption,
                    title=safe_title,
                    performer=performer,
                )
            except Exception:
                # answer_audio ishlamasa — document sifatida yuborish
                await msg.answer_document(
                    document=FSInputFile(file_path),
                    caption=caption,
                )

        logger.info(f"[Music] Audio yuborildi: {safe_title} — {performer} ({file_size_mb:.1f}MB)")
        return True

    except Exception as e:
        logger.error(f"[Music] Audio yuklab yuborish xatosi: {e}")
        try:
            await loading_text.delete()
        except Exception:
            pass
        return False

    finally:
        if file_path_to_cleanup:
            cleanup_file(file_path_to_cleanup)


# ============ Obuna tekshirish ============

async def _check_subscription(message: Message) -> bool:
    """Obuna tekshirish. True = davom etish mumkin"""
    if config.bot.is_admin(message.from_user.id):
        return True

    is_subscribed, unsubscribed = await SubscriptionService.is_subscribed(
        message.bot, message.from_user.id
    )
    if not is_subscribed:
        from app.keyboards.inline import subscription_check_kb
        from app.utils.formatter import format_subscription_required
        text = format_subscription_required(unsubscribed)
        kb = subscription_check_kb(unsubscribed)
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return False

    return True


# ============ Audio handler ============

@router.message(F.audio)
async def handle_audio_message(message: Message, state: FSMContext):
    """Foydalanuvchi audio yuborsa — qo'shiqni tanib berish va audio yuborish"""
    if not await _check_subscription(message):
        return

    loading_msg = await message.answer("🎵 <b>Qo'shiq aniqlanmoqda...</b>", parse_mode="HTML")

    temp_dir = tempfile.mkdtemp()
    audio_path = os.path.join(temp_dir, "audio.ogg")

    try:
        file_id = message.audio.file_id
        file = await message.bot.get_file(file_id)

        if not file or not file.file_path:
            await loading_msg.edit_text("❌ <b>Audio faylni yuklab bo'lmadi.</b>", parse_mode="HTML")
            return

        await message.bot.download_file(file.file_path, destination=audio_path)

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            await loading_msg.edit_text("❌ <b>Audio fayl bo'sh.</b>", parse_mode="HTML")
            return

        result = await recognize_audio(audio_path)

        if not result:
            await loading_msg.edit_text(
                "❌ <b>Qo'shiq topilmadi.</b>\n\n"
                "💡 Iltimos, aniqroq audio yuboring (5-10 soniya yetarli).",
                parse_mode="HTML",
            )
            return

        formatted = format_music_result(result)

        if not formatted:
            await loading_msg.edit_text(
                "❌ <b>Qo'shiq topilmadi.</b>\n\n"
                "💡 Iltimos, aniqroq audio yuboring (5-10 soniya yetarli).",
                parse_mode="HTML",
            )
            return

        # YouTube URL ni olish
        track_info = _extract_track_info(result)
        yt_url = track_info.get("youtube_url", "")

        # Qo'shiq ma'lumotlarini keshlash
        cache_key = ""
        kb = None
        if yt_url:
            cache_key = str(hash(yt_url) % 100000000)
            _cache_music(cache_key, track_info, yt_url)
            kb = _make_download_kb(cache_key)

        # Ma'lumotlarni yuborish + download tugmasi
        await loading_msg.edit_text(formatted, parse_mode="HTML", reply_markup=kb)

        # Audio faylni ham zudlik bilan yuklab yuborish
        if yt_url:
            success = await _download_and_send_audio(message, yt_url, track_info)
            if not success:
                await message.answer(
                    "⚠️ <b>Qo'shiqni yuklab bo'lmadi.</b>\n\n"
                    "💡 Yuqoridagi tugma orqali qayta urinib ko'ring.",
                    parse_mode="HTML",
                )

    except Exception as e:
        logger.error(f"[Music] Handler xatosi: {e}")
        try:
            await loading_msg.edit_text(
                "⚠️ <b>Server band.</b> Qayta urinib ko'ring.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            os.rmdir(temp_dir)
        except OSError:
            pass


# ============ Voice handler ============

@router.message(F.voice)
async def handle_voice_message(message: Message, state: FSMContext):
    """Foydalanuvchi voice message yuborsa — qo'shiqni tanib berish va audio yuborish"""
    if not await _check_subscription(message):
        return

    loading_msg = await message.answer("🎵 <b>Qo'shiq aniqlanmoqda...</b>", parse_mode="HTML")

    temp_dir = tempfile.mkdtemp()
    voice_path = os.path.join(temp_dir, "voice.ogg")

    try:
        file_id = message.voice.file_id
        file = await message.bot.get_file(file_id)

        if not file or not file.file_path:
            await loading_msg.edit_text("❌ <b>Ovozli xabarni yuklab bo'lmadi.</b>", parse_mode="HTML")
            return

        await message.bot.download_file(file.file_path, destination=voice_path)

        if not os.path.exists(voice_path) or os.path.getsize(voice_path) == 0:
            await loading_msg.edit_text("❌ <b>Ovozli xabar bo'sh.</b>", parse_mode="HTML")
            return

        result = await recognize_audio(voice_path)

        if not result:
            await loading_msg.edit_text(
                "❌ <b>Qo'shiq topilmadi.</b>\n\n"
                "💡 Iltimos, aniqroq audio yuboring (5-10 soniya yetarli).",
                parse_mode="HTML",
            )
            return

        formatted = format_music_result(result)

        if not formatted:
            await loading_msg.edit_text(
                "❌ <b>Qo'shiq topilmadi.</b>\n\n"
                "💡 Iltimos, aniqroq audio yuboring (5-10 soniya yetarli).",
                parse_mode="HTML",
            )
            return

        # YouTube URL ni olish
        track_info = _extract_track_info(result)
        yt_url = track_info.get("youtube_url", "")

        # Qo'shiq ma'lumotlarini keshlash
        cache_key = ""
        kb = None
        if yt_url:
            cache_key = str(hash(yt_url) % 100000000)
            _cache_music(cache_key, track_info, yt_url)
            kb = _make_download_kb(cache_key)

        # Ma'lumotlarni yuborish + download tugmasi
        await loading_msg.edit_text(formatted, parse_mode="HTML", reply_markup=kb)

        # Audio faylni ham zudlik bilan yuklab yuborish
        if yt_url:
            success = await _download_and_send_audio(message, yt_url, track_info)
            if not success:
                await message.answer(
                    "⚠️ <b>Qo'shiqni yuklab bo'lmadi.</b>\n\n"
                    "💡 Yuqoridagi tugma orqali qayta urinib ko'ring.",
                    parse_mode="HTML",
                )

    except Exception as e:
        logger.error(f"[Music] Voice handler xatosi: {e}")
        try:
            await loading_msg.edit_text(
                "⚠️ <b>Server band.</b> Qayta urinib ko'ring.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(voice_path):
                os.remove(voice_path)
            os.rmdir(temp_dir)
        except OSError:
            pass


# ============ Music download callback ============

@router.callback_query(F.data.startswith("music_dl_"))
async def handle_music_download(callback: CallbackQuery, state: FSMContext):
    """'Qo'shiqni yuklash' tugmasi — audio qayta yuklab yuborish"""
    key = callback.data.replace("music_dl_", "")
    cache_data = _music_cache.get(key)

    if not cache_data:
        await callback.answer("⏰ Qayta audio yuboring.", show_alert=True)
        return

    if time.time() - cache_data.get("cached_at", 0) > _MUSIC_CACHE_TTL:
        del _music_cache[key]
        await callback.answer("⏰ Qayta audio yuboring.", show_alert=True)
        return

    await callback.answer("⏬ Qo'shiq yuklanmoqda...")

    yt_url = cache_data.get("yt_url", "")
    track_info = cache_data.get("track_info", {})

    if not yt_url:
        await callback.message.answer("❌ <b>YouTube link topilmadi.</b>", parse_mode="HTML")
        return

    success = await _download_and_send_audio(callback, yt_url, track_info, is_callback=True)

    if not success:
        await callback.message.answer(
            "❌ <b>Qo'shiqni yuklab bo'lmadi.</b>\n\n"
            "💡 Keyinroq qayta urinib ko'ring yoki YouTube linkiga o'ting:\n"
            f"▶️ {yt_url}",
            parse_mode="HTML",
        )
