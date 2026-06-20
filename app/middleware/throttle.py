"""Throttle Middleware - Rate limiting"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from app.config import config
from app.utils.helpers import RateLimiter

logger = logging.getLogger(__name__)


class ThrottleMiddleware(BaseMiddleware):
    """Rate limiting middleware"""

    def __init__(self):
        super().__init__()
        self.download_limiter = RateLimiter(
            max_requests=config.rate_limit.downloads,
            period=config.rate_limit.period,
        )
        self.general_limiter = RateLimiter(
            max_requests=30,
            period=60,
        )

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user = event.from_user
        if not user:
            return await handler(event, data)

        # Skip admins from rate limiting
        if config.bot.is_admin(user.id):
            return await handler(event, data)

        # General rate limit
        if not self.general_limiter.is_allowed(user.id):
            if isinstance(event, Message):
                await event.answer("⏳ Iltimos, biroz kuting. Juda ko'p so'rov yubordingiz.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⏳ Biroz kuting", show_alert=True)
            return

        # Download-specific rate limit
        if isinstance(event, Message) and event.text:
            from app.utils.helpers import extract_url_from_text
            if extract_url_from_text(event.text):
                if not self.download_limiter.is_allowed(user.id):
                    remaining = self.download_limiter.get_remaining(user.id)
                    await event.answer(
                        f"⏳ Yuklash limiti tugadi. {config.rate_limit.period} soniya kuting.\n"
                        f"Qolgan: {remaining}"
                    )
                    return

        return await handler(event, data)
