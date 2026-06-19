"""Auth Middleware - Ban check (tez — /start uchun DB yo'q)"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart

from app.config import config

logger = logging.getLogger(__name__)

_ban_cache: Dict[int, bool] = {}
_BAN_CACHE_TTL = 300


class AuthMiddleware(BaseMiddleware):
    """Tez auth — admin o'tkazib yuboriladi, /start DB so'rovsiz."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user = event.from_user
        if not user:
            return await handler(event, data)

        if config.bot.is_admin(user.id):
            data["is_admin"] = True
            return await handler(event, data)

        if isinstance(event, Message) and CommandStart().__call__(event):
            data["is_admin"] = False
            return await handler(event, data)

        uid = user.id
        cached = _ban_cache.get(uid)
        if cached is not None:
            if cached:
                if isinstance(event, Message):
                    await event.answer("🚫 Siz ban qilingansiz. Admin bilan bog'laning.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Siz ban qilingansiz.", show_alert=True)
                return
            data["is_admin"] = False
            return await handler(event, data)

        try:
            from app.database.connection import get_session_factory
            from app.database.repositories.user_repo import UserRepository

            session_factory = await get_session_factory()
            async with session_factory() as session:
                user_repo = UserRepository(session)
                db_user = await user_repo.get_by_id(uid)

                is_banned = bool(db_user and db_user.is_banned)
                _ban_cache[uid] = is_banned

                if is_banned:
                    if isinstance(event, Message):
                        await event.answer("🚫 Siz ban qilingansiz. Admin bilan bog'laning.")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("🚫 Siz ban qilingansiz.", show_alert=True)
                    return

                data["db_user"] = db_user
                data["is_admin"] = False
        except Exception as e:
            logger.error(f"Auth error: {e}")
            data["is_admin"] = False

        return await handler(event, data)