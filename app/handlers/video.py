"""Video Handler - Video download processing"""

import asyncio
import hashlib
import logging
import os
from typing import Dict

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.services.download_service import DownloadService
from app.keyboards.inline import quality_select_kb, mp3_download_kb, back_to_main_kb
from app.utils.downloader import (
    detect_platform, is_video_url,
    format_file_size, cleanup_file,
)
from app.utils.formatter import (
    format_video_info, format_video_caption, format_error, bold,
)

logger = logging.getLogger(__name__)

router = Router()

_video_cache: Dict[str, dict] = {}
_url_cache: Dict[str, str] = {}

# ⚠️ O'zingizning qum soat stiker ID laringizni shu yerga yozing!
# Sticker ID ni olish: stikerni @StickerDownloadBot ga yuboring
_HOURGLASS_STICKERS = [
    "CAACAgIAAxkBAAIKbmZ85pKLMdqjGsMCWfNX-TRxNF7uAAIeAAPANk8TBPbxr4aSKSQeBA",
    "CAACAgIAAxkBAAIKb2Z85qKOMC1R9mvYEkCs_MDSZgvfAAIhAAPANk8TBGdB2YUflEgeBA",
    "CAACAgIAAxkBAAIKcGZ85ry7m2-GxLBVPiKABJaZDLQqAAIgAAPANk8TBGZLHwX01EgeBA",
    "CAACAgIAAxkBAAIKcWZ85sF_FqqWQ9PnWZUBMR6S-QNPAAIfAAPANk8TBGZVDJPw3EgeBA",
]


def _cache_url(url: str) -> str:
    """URL ni MD5 hash ga aylantirib cache qilish (64 byte limit uchun)."""
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    _url_cache[url_hash] = url
    return url_hash


@router.message(StateFilter(None), ~F.text.startswith("/"))
async def handle_video_link(message: Message, state: FSMContext):
    """Handle video link messages"""
    from app.utils.helpers import extract_url_from_text as extract_url

    url = extract_url(message.text or "")

    if not url:
        return

    if not is_video_url(url):
        await message.answer(format_error("invalid_link"), parse_mode="HTML")
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
        logger.error(f"User register error: {e}")

    # Hourglass sticker animatsiyasi boshlash
    sticker_msg = None
    animation_task = None
    if _HOURGLASS_STICKERS:
        try:
            sticker_msg = await message.answer_sticker(_HOURGLASS_STICKERS[0])
            animation_task = asyncio.create_task(
                _animate_hourglass(message.bot, sticker_msg)
            )
        except Exception as e:
            logger.warning(f"Sticker send failed: {e}")

    try:
        result = await DownloadService.process_url(url)

        if result is None:
            if animation_task:
                animation_task.cancel()
            if sticker_msg:
                try:
                    await sticker_msg.delete()
                except Exception:
                    pass
            await message.answer(format_error("download_error"), parse_mode="HTML")
            return

        # Animatsiyani to'xtatish va sticker ni o'chirish
        if animation_task:
            animation_task.cancel()
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass

        # Video info ni cache qilish
        video_id = result["info"].get("id", str(hash(url)))
        _video_cache[video_id] = {
            "url": url,
            "info": result["info"],
            "platform": result["platform"],
        }

        # Cache tozash
        if len(_video_cache) > 100:
            oldest = list(_video_cache.keys())[:50]
            for k in oldest:
                del _video_cache[k]

        # Video info ko'rsatish
        text = format_video_info(result["info"], result["platform"])
        kb = quality_select_kb(video_id, result["available_qualities"])

        # Thumbnail bilan
        thumbnail_url = result["info"].get("thumbnail")
        if thumbnail_url:
            try:
                await message.answer_photo(
                    photo=thumbnail_url,
                    caption=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            except Exception:
                await message.answer(text, reply_markup=kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        if animation_task:
            animation_task.cancel()
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass
        logger.error(f"Error processing video: {e}")
        try:
            await message.answer(format_error("server_error"), parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("quality_"))
async def handle_quality_select(callback: CallbackQuery, state: FSMContext):
    """Handle quality selection"""
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("❌ Xatolik", show_alert=True)
        return

    video_id = parts[1]
    quality = parts[2]
    audio_only = quality == "mp3"

    video_data = _video_cache.get(video_id)
    if not video_data:
        await callback.answer("⏰ Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    url = video_data["url"]

    # Hourglass sticker boshlash
    sticker_msg = None
    animation_task = None
    if _HOURGLASS_STICKERS:
        try:
            sticker_msg = await callback.message.answer_sticker(_HOURGLASS_STICKERS[0])
            animation_task = asyncio.create_task(
                _animate_hourglass(callback.bot, sticker_msg)
            )
        except Exception:
            pass

    try:
        quality_str = quality if not audio_only else "720p"
        result = await DownloadService.download(
            url=url, quality=quality_str,
            audio_only=audio_only, user_id=callback.from_user.id,
        )

        if animation_task:
            animation_task.cancel()
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass

        if result is None:
            await callback.message.answer(format_error("download_error"), parse_mode="HTML")
            return

        file_path = result["file_path"]
        file_size_mb = result["file_size_mb"]

        # Caption
        caption = "🤖 @UzVideoSaveBot"

        # MP3 cache key
        mp3_kb = None
        if not audio_only:
            cache_key = _cache_url(url)
            mp3_kb = mp3_download_kb(cache_key)

        # Send the file
        try:
            if audio_only:
                await callback.message.answer_audio(
                    audio=FSInputFile(file_path), caption=caption,
                )
            elif file_size_mb > config.download.max_file_size_mb:
                await callback.message.answer_document(
                    document=FSInputFile(file_path), caption=caption,
                )
            else:
                try:
                    await callback.message.answer_video(
                        video=FSInputFile(file_path), caption=caption,
                    )
                except Exception:
                    await callback.message.answer_document(
                        document=FSInputFile(file_path), caption=caption,
                    )

            # MP3 tugmasini alohida xabarda yuborish
            if mp3_kb:
                await callback.message.answer(
                    "🎵 MP3 versiyasini ham yuklashni xohlaysizmi?",
                    reply_markup=mp3_kb
                )

        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await callback.message.answer(format_error("server_error"), parse_mode="HTML")
        finally:
            cleanup_file(file_path)

    except Exception as e:
        if animation_task:
            animation_task.cancel()
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass
        logger.error(f"Error downloading: {e}")
        try:
            await callback.message.answer(format_error("server_error"), parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("mp3_"))
async def handle_mp3_download(callback: CallbackQuery, state: FSMContext):
    """MP3 yuklash — cache key orqali URL ni topish."""
    cache_key = callback.data.replace("mp3_", "")
    url = _url_cache.get(cache_key)

    if not url:
        await callback.answer("⏰ Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    await callback.answer("🎵 MP3 yuklanmoqda...")

    try:
        result = await DownloadService.download(
            url=url, quality="720p",
            audio_only=True, user_id=callback.from_user.id,
        )

        if result is None:
            await callback.message.answer(format_error("download_error"), parse_mode="HTML")
            return

        file_path = result["file_path"]

        await callback.message.answer_audio(
            audio=FSInputFile(file_path),
            caption="🤖 @UzVideoSaveBot",
        )

        cleanup_file(file_path)

    except Exception as e:
        logger.error(f"MP3 download error: {e}")
        await callback.message.answer(format_error("server_error"), parse_mode="HTML")


@router.callback_query(F.data == "cancel_download")
async def cancel_download(callback: CallbackQuery, state: FSMContext):
    """Cancel download"""
    try:
        await callback.message.edit_text("❌ Yuklash bekor qilindi.")
    except Exception:
        await callback.message.answer("❌ Yuklash bekor qilindi.")
    await callback.answer()


async def _animate_hourglass(bot: Bot, sticker_msg):
    """Hourglass sticker animatsiyasi — video tayyor bo'lguncha aylanib turadi."""
    if not _HOURGLASS_STICKERS:
        return

    idx = 0
    try:
        while True:
            await asyncio.sleep(2)
            idx = (idx + 1) % len(_HOURGLASS_STICKERS)
            try:
                await bot.edit_message_media(
                    chat_id=sticker_msg.chat.id,
                    message_id=sticker_msg.message_id,
                    media={"type": "sticker", "media": _HOURGLASS_STICKERS[idx]},
                )
            except Exception:
                break
    except asyncio.CancelledError:
        pass