

import asyncio
import hashlib
import logging
import time
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
from app.keyboards.inline import back_to_main_kb, mp3_download_kb
from app.utils.downloader import (
    detect_platform, is_video_url,
    cleanup_file,
)
from app.utils.formatter import (
    format_video_caption,
    format_error, bold,
)
from app.utils.helpers import extract_url_from_text as extract_url

logger = logging.getLogger(__name__)

router = Router()

# URL larni vaqtincha saqlash (MP3 uchun) — kalit: qisqa hash
_url_cache: Dict[str, str] = {}

# Qum soat stiker ID (Telegram rasmiy hourglass stiker seti)
_HOURGLASS_STICKERS = [
    "CAACAgUAAxkBAAIIOmZb8ABHxiG0xSRf9v7rOeBBvlXxAAIJAAP20QgUw2lKRaXhOG2aBA",
    "CAACAgUAAxkBAAIIO2Zb8AAyGQf0xSWbLrAOguAOo7LR3AAJOAAP20QgU3ABErShD-nnBA",
    "CAACAgUAAxkBAAIIPGZb8AAyGRKwMRUyg0Uj2hAARxSRmAAJLAAP20QgU_7mdkWj7KyBA",
    "CAACAgUAAxkBAAIIPWZb8AAyGSaQMRUgSlT1bgABh7G-iAAJMAAP20QgU6YsEqDqv2rBA",
]


def _cache_url(url: str) -> str:
    """URL ni keshga saqlash va qisqa kalit qaytarish."""
    key = hashlib.md5(url.encode()).hexdigest()[:10]
    _url_cache[key] = url

    # Keshni 100 tadan ortiqsa tozalash
    if len(_url_cache) > 100:
        keys_to_remove = list(_url_cache.keys())[:50]
        for k in keys_to_remove:
            del _url_cache[k]

    return key


async def _animate_hourglass(bot: Bot, sticker_msg):
    """Qum soat stiker animatsiyasi — video chiqguncha aylanaveradi."""
    idx = 0
    try:
        while True:
            await asyncio.sleep(1)
            idx = (idx + 1) % len(_HOURGLASS_STICKERS)
            try:
                await sticker_msg.edit_media(
                    {"type": "sticker", "media": _HOURGLASS_STICKERS[idx]}
                )
            except Exception:
                break
    except asyncio.CancelledError:
        pass


@router.message(StateFilter(None), ~F.text.startswith("/"))
async def handle_video_link(message: Message, state: FSMContext):
    """Foydalanuvchi link yuborsa — to'g'ridan-to'g'ri yuklab video yuborish.

    1. Link tekshiriladi
    2. ⏳ Qum soat STIKER chiqadi va aylanaveradi
    3. Video yuklanadi
    4. Video yuboriladi (stiker hali ko'rinadi)
    5. Stiker yoqoladi
    6. Video ostida MP3 tugmasi
    """
    url = extract_url(message.text or "")

    if not url:
        return

    if not is_video_url(url):
        await message.answer(
            format_error("invalid_link"),
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

    # ⏳ Qum soat STIKER yuborish va animatsiya boshlash
    try:
        sticker_msg = await message.answer_sticker(_HOURGLASS_STICKERS[0])
    except Exception:
        # Stiker yuborilmasa — matn bilan
        sticker_msg = await message.answer("⏳ Yuklab olinmoqda...")
        _HOURGLASS_STICKERS.clear()  # stiker yo'q deb belgila

    animation_task = asyncio.create_task(_animate_hourglass(message.bot, sticker_msg))

    try:
        # Video yuklash
        result = await DownloadService.download(
            url=url,
            quality="720p",
            audio_only=False,
            user_id=message.from_user.id,
        )

        if result is None:
            animation_task.cancel()
            try:
                await sticker_msg.delete()
            except Exception:
                pass
            await message.answer(
                format_error("download_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
            return

        file_path = result["file_path"]
        file_size_mb = result["file_size_mb"]

        # Animatsiyani to'xtatamiz (stiker hali ko'rinadi)
        animation_task.cancel()

        # MP3 uchun URL ni keshga saqlash
        cache_key = _cache_url(url)

        # Video YUBORAMIZ — stiker hali turadi
        caption = format_video_caption(result["info"])
        mp3_kb = mp3_download_kb(cache_key)

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
                        reply_markup=mp3_kb,
                    )
                except Exception:
                    await message.answer_document(
                        document=FSInputFile(file_path),
                        caption=caption,
                        reply_markup=mp3_kb,
                    )
        except Exception as e:
            logger.error(f"Video yuborish xatosi: {e}")
            try:
                await message.answer(
                    format_error("server_error"),
                    reply_markup=back_to_main_kb(),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        finally:
            cleanup_file(file_path)

        # Video chatda ko'rindi — ⏳ stikerni endi o'chiramiz
        try:
            await sticker_msg.delete()
        except Exception:
            pass

    except Exception as e:
        animation_task.cancel()
        logger.error(f"Yuklash xatosi: {e}")
        try:
            await sticker_msg.delete()
        except Exception:
            pass
        try:
            await message.answer(
                format_error("server_error"),
                reply_markup=back_to_main_kb(),
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("mp3_"))
async def handle_mp3_download(callback: CallbackQuery, state: FSMContext):
    """MP3 yuklash tugmasi bosildi — audio yuklab yuborish."""
    cache_key = callback.data[4:]  # "mp3_" olib tashlash
    url = _url_cache.get(cache_key)

    if not url:
        await callback.answer("⏰ Sessiya tugadi. Qayta link yuboring.", show_alert=True)
        return

    await callback.answer("🎵 MP3 yuklanmoqda...")

    # ⏳ Qum soat stiker animatsiyasi
    try:
        sticker_msg = await callback.message.answer_sticker(_HOURGLASS_STICKERS[0])
    except Exception:
        sticker_msg = await callback.message.answer("⏳ MP3 yuklab olinmoqda...")
        _HOURGLASS_STICKERS.clear()

    animation_task = asyncio.create_task(_animate_hourglass(callback.bot, sticker_msg))

    try:
        result = await DownloadService.download(
            url=url,
            quality="720p",
            audio_only=True,
            user_id=callback.from_user.id,
        )

        if result is None:
            animation_task.cancel()
            try:
                await sticker_msg.delete()
            except Exception:
                pass
            await callback.message.answer(
                "❌ MP3 yuklab bo'lmadi.",
                reply_markup=back_to_main_kb(),
            )
            return

        file_path = result["file_path"]

        # Animatsiyani to'xtatamiz
        animation_task.cancel()

        # Audio YUBORAMIZ — stiker hali turadi
        try:
            await callback.message.answer_audio(
                audio=FSInputFile(file_path),
                caption="🎵 MP3 Audio\n🤖 @UzVideoSaveBot",
            )
        except Exception as e:
            logger.error(f"Audio yuborish xatosi: {e}")
        finally:
            cleanup_file(file_path)

        # Audio chatda ko'rindi — stikerni o'chiramiz
        try:
            await sticker_msg.delete()
        except Exception:
            pass

    except Exception as e:
        animation_task.cancel()
        logger.error(f"MP3 yuklash xatosi: {e}")
        try:
            await sticker_msg.delete()
        except Exception:
            pass
        try:
            await callback.message.answer(
                "❌ MP3 yuklash xatosi.",
                reply_markup=back_to_main_kb(),
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
    """Download tugmasi bosildi — hech narsa qilmaymiz (link yuborish yetarli)"""
    await callback.message.delete()