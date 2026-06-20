
import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.config import config
from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.channel_repo import ChannelRepository
from app.keyboards.inline import subscription_check_kb
from app.utils.formatter import format_subscription_required

logger = logging.getLogger(__name__)

# Ban cache: {user_id: (is_banned, timestamp)}
_ban_cache: Dict[int, tuple] = {}
_BAN_CACHE_TTL = 60  # seconds


class AuthMiddleware(BaseMiddleware):
    """Middleware for checking user authentication, bans, and subscriptions"""

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

        # Check ban cache first
        now = time.time()
        cached = _ban_cache.get(user.id)
        if cached and (now - cached[1]) < _BAN_CACHE_TTL:
            if cached[0]:  # is_banned
                if isinstance(event, Message):
                    await event.answer("🚫 Siz ban qilingansiz. Admin bilan bog'laning.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Siz ban qilingansiz.", show_alert=True)
                return
            # Not banned, skip DB check
            data["db_user"] = None
            data["is_admin"] = False
            return await handler(event, data)

        # Cache miss or expired — check DB
        try:
            session_factory = await get_session_factory()
            async with session_factory() as session:
                user_repo = UserRepository(session)
                db_user = await user_repo.get_by_id(user.id)

                # Check if banned
                if db_user and db_user.is_banned:
                    _ban_cache[user.id] = (True, now)
                    if isinstance(event, Message):
                        await event.answer("🚫 Siz ban qilingansiz. Admin bilan bog'laning.")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("🚫 Siz ban qilingansiz.", show_alert=True)
                    return

                # Update cache
                _ban_cache[user.id] = (False, now)

                # Store user in data
                data["db_user"] = db_user
                data["is_admin"] = False
        except Exception as e:
            logger.error(f"AuthMiddleware DB error: {e}")
            data["db_user"] = None
            data["is_admin"] = False

        # For specific callbacks, skip subscription check
        if isinstance(event, CallbackQuery):
            skip_callbacks = {"check_subscription", "back_main", "help", "profile", "premium"}
            if event.data in skip_callbacks:
                return await handler(event, data)

        return await handler(event, data)


class SubscriptionMiddleware(BaseMiddleware):
    """Middleware for checking channel subscriptions (only for download-related actions)"""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user = event.from_user
        if not user:
            return await handler(event, data)

        # Skip admin from subscription checks
        if config.bot.is_admin(user.id):
            return await handler(event, data)

        # Only check subscription for video download actions
        if isinstance(event, CallbackQuery):
            check_callbacks = {"download", "check_subscription"}
            if event.data not in check_callbacks:
                return await handler(event, data)

        if isinstance(event, Message):
            # Check if message contains a URL
            from app.utils.helpers import extract_url_from_text
            if event.text and not extract_url_from_text(event.text):
                return await handler(event, data)

        # Check Telegram channel subscriptions
        session_factory = await get_session_factory()

        async with session_factory() as session:
            channel_repo = ChannelRepository(session)
            telegram_channels = await channel_repo.get_telegram_channels()

            if not telegram_channels:
                return await handler(event, data)

            unsubscribed = []
            bot = data.get("bot") or (event.bot if hasattr(event, 'bot') else None)

            if bot:
                from aiogram.enums import ChatMemberStatus
                for ch in telegram_channels:
                    try:
                        channel_id = ch.channel_id or ch.channel_link
                        if channel_id and channel_id.startswith("@"):
                            channel_id = channel_id

                        member = await bot.get_chat_member(channel_id, user.id)
                        if str(member.status) not in (
                            str(ChatMemberStatus.MEMBER),
                            str(ChatMemberStatus.ADMINISTRATOR),
                            str(ChatMemberStatus.OWNER),
                        ):
                            unsubscribed.append(ch)
                    except Exception as e:
                        logger.warning(f"Could not check subscription for {ch.channel_link}: {e}")
                        # If we can't check, assume not subscribed
                        unsubscribed.append(ch)

            if unsubscribed:
                text = format_subscription_required(unsubscribed)
                kb = subscription_check_kb(unsubscribed)

                if isinstance(event, Message):
                    await event.answer(text, reply_markup=kb, parse_mode="HTML")
                elif isinstance(event, CallbackQuery):
                    await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
                return

        return await handler(event, data)