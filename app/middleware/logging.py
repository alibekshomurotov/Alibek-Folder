"""Logging Middleware"""

import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    """Middleware for logging all bot interactions"""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user = event.from_user
        start_time = time.time()

        # Log incoming event
        if isinstance(event, Message):
            logger.info(
                f"Message from {user.id} (@{user.username}): "
                f"{event.text[:100] if event.text else '[non-text]'}"
            )
        elif isinstance(event, CallbackQuery):
            logger.info(
                f"Callback from {user.id} (@{user.username}): {event.data}"
            )

        try:
            result = await handler(event, data)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"Error handling event from {user.id}: {e} "
                f"(took {elapsed:.2f}s)"
            )
            raise

        elapsed = time.time() - start_time
        if elapsed > 5:
            logger.warning(f"Slow handler: {elapsed:.2f}s for user {user.id}")

        return result
