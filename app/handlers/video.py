"""Video Handler — Stiker animatsiya + video yuklash"""

import asyncio
import hashlib
import logging
import os
from typing import Dict, List, Optional

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.services.download_service import DownloadService
from app.keyboards.inline import video_result_kb
from app.utils.downloader import is_video_url, cleanup_file
from app.utils.formatter import format_error

logger = logging.getLogger(__name__)

router = Router()

# MP3 uchun URL cache
_url_cache: Dict[str, str] = {}

# Stikerlar (startup da to'ldiriladi)
_hourglass_stickers: List[str] = []


def _cache_url(url: str) -> str:
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    _url_cache[url_hash] = url
    return url_hash


async def load_hourglass_stickers(bot: Bot):
    """Bot ishga tushganda stikerlarni yuklash."""
    global _hourglass_stickers

    # 1-prioritet: env var (admin Render'da qo'ygan HOURGLASS_STICKERS)
    env_stickers = os.getenv("HOURGLASS_STICKERS", "")
    if env_stickers:
        ids = [s.strip() for s in env_stickers.split(",") if s.strip()]
        if ids:
            _hourglass_stickers = ids
            logger.info(f"✅ Stikerlar env dan yuklandi ({len(ids)} ta)")
            return

    # 2-prioritet: Telegram stiker setlarini qidirish
    sets_to_try = [
        "hourglass_animated",
        "loadinganimation",
        "loading_stickers",
        "waitaminute",
        "loading",
        "hourglass",
    ]

    for set_name in sets_to_try:
        try:
            sticker_set = await bot.get_sticker_set(set_name)
            animated = [s.file_id for s in sticker_set.stickers if s.is_animated]
            if animated:
                _hourglass_stickers = animated[:4]
                logger.info(f"✅ Stikerlar '{set_name}' dan topildi ({len(animated)} ta)")
                return
        except Exception:
            continue

    logger.warning("⚠️ Hech qanday stiker topilmadi, matn animatsiya ishlatiladi")


async def _animate_sticker(bot: Bot, chat_id: int, message_id: int, stop_event: asyncio.Event):
    """Stiker animatsiyasi — bir nechta stiker bo'lsa almashtirib turadi."""
    if not _hourglass_stickers:
        return

    # Agar faqat 1 stiker bo'lsa — o'chirib qayta yuborib animatsiyani takrorlaymiz
    if len(_hourglass_stickers) == 1:
        sticker_id = _hourglass_stickers[0]
        try:
            while not stop_event.is_set():
                await asyncio.sleep(3.0)
                if stop_event.is_set():
                    break
                # Eski stikerni o'chirish
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    return
                # Yangi stiker yuborish (animatsiya qayta boshlanadi)
                if not stop_event.is_set():
                    try:
                        new_msg = await bot.send_sticker(chat_id, sticker_id)
                        message_id = new_msg.message_id
                    except Exception:
                        return
        except asyncio.CancelledError:
            pass
        return

    # Ko'p stiker bo'lsa — edit_message_media bilan almashtirish
    idx = 0
    try:
        while not stop_event.is_set():
            await asyncio.sleep(1.5)
            idx = (idx + 1) % len(_hourglass_stickers)
            try:
                await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media={"type": "sticker", "media": _hourglass_stickers[idx]},
                )
            except Exception:
                return
    except asyncio.CancelledError:
        pass


async def _animate_text(bot: Bot, chat_id: int, message_id: int, stop_event: asyncio.Event):
    """Matnli animatsiya fallback."""
    steps = [
        "⏳ Video yuklanmoqda .",
        "⏳ Video yuklanmoqda . .",
        "⏳ Video yuklanmoqda . . .",
        "⏳ Video qayta ishlanmoqda . . .",
        "⏳ Sifat optimallashtirilmoqda . . .",
        "⏳ Deyarli tayyor . . .",
    ]
    idx = 0
    try:
        while not stop_event.is_set():
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=steps[idx % len(steps)],
                )
            except Exception:
                return
            idx += 1
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        pass


async def _start_animation(bot: Bot, chat_id: int):
    """Animatsiya boshlash — stiker yoki matn."""
    stop_event = asyncio.Event()
    task = None
    msg = None

    if _hourglass_stickers:
        try:
            msg = await bot.send_sticker(chat_id, _hourglass_stickers[0])
            logger.info(f"⏳ Stiker yuborildi (chat_id={chat_id}, stickers={len(_hourglass_stickers)} ta)")
            task = asyncio.create_task(
                _animate_sticker(bot, chat_id, msg.message_id, stop_event)
            )
            return stop_event, task, msg
        except Exception as e:
            logger.warning(f"Stiker yuborish xatosi: {e}, matn animatsiyaga o'tish")

    # Fallback: matn animatsiya
    try:
        msg = await bot.send_message(chat_id, "⏳ Video yuklanmoqda . . .")
        task = asyncio.create_task(_animate_text(bot, chat_id, msg.message_id, stop_event))
    except Exception:
        pass

    return stop_event, task, msg


async def _stop_animation(bot: Bot, stop_event, task, msg):
    """Animatsiyani to'xtatib xabarni o'chirish."""
    stop_event.set()
    if task:
        task.cancel()
        try:
            await task
        except Exception:
            pass
    if msg:
        try:
            await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except Exception:
            pass


@router.message(F.text)
async def handle_video_link(message: Message, state: FSMContext):
    """Link yuborilsa: stiker animatsiya → video."""
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

    # Animatsiya boshlash
    stop_event, anim_task, anim_msg = await _start_animation(message.bot, message.chat.id)

    try:
        result = await DownloadService.download(
            url=url,
            quality="720p",
            audio_only=False,
            user_id=message.from_user.id,
        )

        # Animatsiyani to'xtatib o'chirish
        await _stop_animation(message.bot, stop_event, anim_task, anim_msg)

        if result is None:
            await message.answer(format_error("download_error"), parse_mode="HTML")
            return

        file_path = result["file_path"]
        file_size_mb = result["file_size_mb"]
        caption = "@UzVideoSaveBot"

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
        await _stop_animation(message.bot, stop_event, anim_task, anim_msg)
        logger.error(f"Error processing video: {e}")
        try:
            await message.answer(format_error("server_error"), parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("mp3_"))
async def handle_mp3_download(callback: CallbackQuery, state: FSMContext):
    """MP3 yuklash."""
    cache_key = callback.data.replace("mp3_", "")
    url = _url_cache.get(cache_key)

    if not url:
        await callback.answer("Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    await callback.answer("MP3 yuklanmoqda...")

    stop_event, anim_task, anim_msg = await _start_animation(
        callback.bot, callback.message.chat.id
    )

    try:
        result = await DownloadService.download(
            url=url,
            quality="720p",
            audio_only=True,
            user_id=callback.from_user.id,
        )

        await _stop_animation(callback.bot, stop_event, anim_task, anim_msg)

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
        await _stop_animation(callback.bot, stop_event, anim_task, anim_msg)
        logger.error(f"MP3 download error: {e}")
        await callback.message.answer(format_error("server_error"), parse_mode="HTML")

    await callback.answer()