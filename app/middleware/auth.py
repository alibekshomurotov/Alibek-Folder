"""Auth Middleware - Ban check only (no subscription required, all users free)"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Middleware for checking user bans only - no subscription or rate limits"""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user = event.from_user
        if not user:
            return await handler(event, data)

        # Skip admin from checks
        if config.bot.is_admin(user.id):
            data["is_admin"] = True
            return await handler(event, data)

        session_factory = await get_session_factory()

        async with session_factory() as session:
            user_repo = UserRepository(session)
            db_user = await user_repo.get_by_id(user.id)

            # Check if banned
            if db_user and db_user.is_banned:
                if isinstance(event, Message):
                    await event.answer("🚫 Siz ban qilingansiz. Admin bilan bog'laning.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Siz ban qilingansiz.", show_alert=True)
                return

            # Store user in data
            data["db_user"] = db_user
            data["is_admin"] = False

        return await handler(event, data)
