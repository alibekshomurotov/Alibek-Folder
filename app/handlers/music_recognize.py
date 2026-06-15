import logging
import os
import tempfile

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

try:
    from shazamio import Shazam
except ImportError:
    Shazam = None  # type: ignore[assignment, misc]

from app.config import config
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

router = Router()


# ============ Shazam yordamchi funksiyalar ============

async def recognize_audio(file_path: str) -> dict | None:
    """Shazamio orqali audioni tanish"""
    try:
        from shazamio import Shazam
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
    """Foydalanuvchi audio yuborsa — qo'shiqni tanib berish"""
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

        await loading_msg.edit_text(formatted, parse_mode="HTML")

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
    """Foydalanuvchi voice message yuborsa — qo'shiqni tanib berish"""
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

        await loading_msg.edit_text(formatted, parse_mode="HTML")

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
