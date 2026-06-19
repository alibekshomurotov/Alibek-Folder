"""Video Handler - Video download processing (simplified: auto download + MP3 + Profil)"""

import asyncio
import hashlib
import logging
import os
from typing import Dict

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.services.download_service import DownloadService
from app.keyboards.inline import video_result_kb
from app.utils.downloader import (
    detect_platform, is_video_url,
    cleanup_file,
)
from app.utils.formatter import format_error

logger = logging.getLogger(__name__)

router = Router()

# MP3 uchun URL cache (callback_data 64 byte limit)
_url_cache: Dict[str, str] = {}

# Qum soat stikerlari (o'zingiznikiga almashtiring)
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


@router.message(F.text)
async def handle_video_link(message: Message, state: FSMContext):
    """Link yuborilsa: qum soat stikeri aylanadi -> video + MP3 + Profil tugmalar bir xabarda."""
    from app.utils.helpers import extract_url_from_text as extract_url

    url = extract_url(message.text or "")

    if not url:
        return

    if not is_video_url(url):
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

    # Qum soat stikeri + animatsiya boshlash
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
        # AVTOMATIK yuklash
        result = await DownloadService.download(
            url=url,
            quality="720p",
            audio_only=False,
            user_id=message.from_user.id,
        )

        # Qum soatni to'xtatish va o'chirish
        if animation_task:
            animation_task.cancel()
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass

        if result is None:
            await message.answer(format_error("download_error"), parse_mode="HTML")
            return

        file_path = result["file_path"]
        file_size_mb = result["file_size_mb"]
        caption = "@UzVideoSaveBot"

        # Video + MP3 + Profil bir xabarda
        cache_key = _cache_url(url)
        kb = video_result_kb(cache_key)

        try:
            if file_size_mb > config.download.max_file_size_mb:
                await message.answer_document(
                    document=FSInputFile(file_path),
                    caption=caption,
                    reply_markup=kb,
                )
            else:
                try:
                    await message.answer_video(
                        video=FSInputFile(file_path),
                        caption=caption,
                        reply_markup=kb,
                    )
                except Exception:
                    await message.answer_document(
                        document=FSInputFile(file_path),
                        caption=caption,
                        reply_markup=kb,
                    )
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await message.answer(format_error("server_error"), parse_mode="HTML")
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
        logger.error(f"Error processing video: {e}")
        try:
            await message.answer(format_error("server_error"), parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("mp3_"))
async def handle_mp3_download(callback: CallbackQuery, state: FSMContext):
    """MP3 yuklash - cache key orqali URL ni topish."""
    cache_key = callback.data.replace("mp3_", "")
    url = _url_cache.get(cache_key)

    if not url:
        await callback.answer("Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    await callback.answer("MP3 yuklanmoqda...")

    # Qum soat stikeri
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
        result = await DownloadService.download(
            url=url,
            quality="720p",
            audio_only=True,
            user_id=callback.from_user.id,
        )

        # Qum soatni to'xtatish va o'chirish
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

        await callback.message.answer_audio(
            audio=FSInputFile(file_path),
            caption="@UzVideoSaveBot",
        )

        cleanup_file(file_path)

    except Exception as e:
        if animation_task:
            animation_task.cancel()
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass
        logger.error(f"MP3 download error: {e}")
        await callback.message.answer(format_error("server_error"), parse_mode="HTML")

    await callback.answer()


async def _animate_hourglass(bot: Bot, sticker_msg):
    """Qum soat stikeri animatsiyasi — video tayyor bo'lguncha aylanib turadi."""
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