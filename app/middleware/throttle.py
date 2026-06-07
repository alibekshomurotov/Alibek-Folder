"""Throttle Middleware - Minimal rate limiting (anti-spam only)"""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)


class ThrottleMiddleware(BaseMiddleware):
    """Very permissive rate limiting - only prevents spam/abuse, no download limits"""

    def __init__(self):
        super().__init__()
        # Very generous limits - just anti-spam protection
        self.general_limiter = {}
        self.min_interval = 0.5  # Minimum 0.5 seconds between messages (anti-flood only)

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user = event.from_user
        if not user:
            return await handler(event, data)

        # No rate limiting at all - everyone is free to use as much as they want
        return await handler(event, data)
