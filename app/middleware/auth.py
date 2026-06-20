"""Auth Middleware - Ban check (optimized — /start uchun DB yo'q)"""

import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart

from app.config import config

logger = logging.getLogger(__name__)

# Ban cache: {user_id: {"banned": bool, "cached_at": float}}
_ban_cache: Dict[int, Dict] = {}
_BAN_CACHE_TTL = 60  # 1 daqiqa cache


class AuthMiddleware(BaseMiddleware):
    """Middleware for checking user authentication and bans - with cache!"""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user = event.from_user
        if not user:
            return await handler(event, data)

        # Admin - tez o'tkazish
        if config.bot.is_admin(user.id):
            data["is_admin"] = True
            return await handler(event, data)

        # /start — DB so'rov yo'q, to'g'ridan-to'g'ri o'tkaz
        if isinstance(event, Message) and CommandStart().__call__(event):
            data["is_admin"] = False
            return await handler(event, data)

        # Ban tekshirish - cache bilan
        now = time.time()
        cached = _ban_cache.get(user.id)

        if cached and (now - cached["cached_at"]) < _BAN_CACHE_TTL:
            # Cache dan tekshirish (DB so'rov Yo'Q!)
            if cached["banned"]:
                if isinstance(event, Message):
                    await event.answer("🚫 Siz ban qilingansiz. Admin bilan bog'laning.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Siz ban qilingansiz.", show_alert=True)
                return
            data["is_admin"] = False
            return await handler(event, data)

        # Cache yo'q - DB dan tekshirish
        from app.database.connection import get_session_factory
        from app.database.repositories.user_repo import UserRepository

        is_banned = False
        try:
            session_factory = await get_session_factory()
            async with session_factory() as session:
                user_repo = UserRepository(session)
                db_user = await user_repo.get_by_id(user.id)
                if db_user and db_user.is_banned:
                    is_banned = True
        except Exception as e:
            logger.error(f"Auth DB error: {e}")

        # Cache ga yozish
        _ban_cache[user.id] = {
            "banned": is_banned,
            "cached_at": now,
        }

        # Eski cache tozalash
        expired = [uid for uid, val in _ban_cache.items()
                   if now - val["cached_at"] > _BAN_CACHE_TTL]
        for uid in expired:
            del _ban_cache[uid]

        if is_banned:
            if isinstance(event, Message):
                await event.answer("🚫 Siz ban qilingansiz. Admin bilan bog'laning.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Siz ban qilingansiz.", show_alert=True)
            return

        data["is_admin"] = False
        return await handler(event, data)
