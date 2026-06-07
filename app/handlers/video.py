"""Video Handler - Video download processing"""

import asyncio
import hashlib
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
# Subscription removed - all users are free
from app.keyboards.inline import quality_select_kb, back_to_main_kb
from app.utils.downloader import (
    detect_platform, is_video_url,
    cleanup_file,
)
from app.utils.formatter import (
    format_video_info, format_video_caption, format_loading_step,
    format_error, bold,
)
from app.utils.helpers import extract_url_from_text as extract_url

logger = logging.getLogger(__name__)

router = Router()

# Store video info temporarily (in production, use Redis)
# Key: short_hash (8 chars) -> video data
_video_cache: Dict[str, dict] = {}


def _make_cache_key(video_id: str) -> str:
    """Create a short cache key from video_id to avoid Telegram 64-byte callback data limit"""
    return hashlib.md5(video_id.encode()).hexdigest()[:8]


@router.message(StateFilter(None), ~F.text.startswith("/"))
async def handle_video_link(message: Message, state: FSMContext):
    """Handle video link messages"""
    # Skip reply keyboard button texts
    skip_texts = {"📥 Video yuklash", "👤 Profil", "⭐ Premium", "ℹ️ Yordam", "🔧 Admin panel"}
    if message.text in skip_texts:
        return

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

    try:
        # Extract video info
        result = await DownloadService.process_url(url)

        if result is None:
            animation_task.cancel()
            await loading_msg.edit_text(
                format_error("download_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
            return

        # Cancel animation
        animation_task.cancel()

        # Cache video info using short hash key
        original_video_id = result["info"].get("id", str(hash(url)))
        cache_key = _make_cache_key(original_video_id)
        _video_cache[cache_key] = {
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
        kb = quality_select_kb(cache_key, result["available_qualities"])

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
    # Parse callback data: quality_{cache_key}_{quality}
    # cache_key is 8 chars, quality is like "1080p", "720p", "mp3"
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("❌ Xatolik", show_alert=True)
        return

    cache_key = parts[1]
    quality = parts[2]
    audio_only = quality == "mp3"

    # Get cached video info
    video_data = _video_cache.get(cache_key)
    if not video_data:
        await callback.answer(
            "⏰ Sessiya tugadi. Qayta link yuboring.",
            show_alert=True
        )
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
                # Send as audio
                await callback.message.answer_audio(
                    audio=FSInputFile(file_path),
                    caption=f"🎵 MP3 Audio\n🤖 Downloader Pro",
                )
            elif file_size_mb > config.download.max_file_size_mb:
                # Send as document if too large
                await callback.message.answer_document(
                    document=FSInputFile(file_path),
                    caption=format_video_caption(result["info"], quality.upper()),
                )
            else:
                # Send as video
                try:
                    await callback.message.answer_video(
                        video=FSInputFile(file_path),
                        caption=format_video_caption(result["info"], quality.upper()),
                    )
                except Exception:
                    # If send_video fails, try as document
                    await callback.message.answer_document(
                        document=FSInputFile(file_path),
                        caption=format_video_caption(result["info"], quality.upper()),
                    )

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
        f"  🧵 Threads\n\n"
        f"🎵 MP3 uchun ham link yuboring va Audio MP3 tugmasini bosing!",
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
