import asyncio
import logging
from typing import List, Tuple

from aiogram import Bot
from aiogram.enums import ChatMemberStatus

from app.database.connection import get_session_factory
from app.database.repositories.channel_repo import ChannelRepository
from app.database.models import Channel

logger = logging.getLogger(__name__)


class SubscriptionService:
    """Service for managing channel subscriptions"""

    @staticmethod
    async def get_unsubscribed_channels(bot: Bot, user_id: int) -> List[Channel]:
        """Get list of Telegram channels the user hasn't subscribed to - PARALLEL!"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            channel_repo = ChannelRepository(session)
            telegram_channels = await channel_repo.get_telegram_channels()

            if not telegram_channels:
                return []

            # Barcha kanallarni PARALLEL tekshirish (1-2 soniya o'rniga)
            async def _check_channel(ch: Channel) -> tuple:
                """Bitta kanalni tekshirish"""
                try:
                    channel_id = ch.channel_id or ch.channel_link
                    member = await bot.get_chat_member(channel_id, user_id)
                    status = str(member.status)
                    if status in (
                        str(ChatMemberStatus.MEMBER),
                        str(ChatMemberStatus.ADMINISTRATOR),
                        str(ChatMemberStatus.OWNER),
                    ):
                        return (ch, False)  # obuna bo'lgan
                    return (ch, True)  # obuna bo'lmagan
                except Exception as e:
                    logger.warning(f"Could not check subscription for {ch.channel_link}: {e}")
                    return (ch, True)  # xato - obuna bo'lmagan deb hisoblaymiz

            # Parallel tekshirish - barcha kanallar bir vaqtda!
            results = await asyncio.gather(
                *[_check_channel(ch) for ch in telegram_channels],
                return_exceptions=False,
            )

            unsubscribed = [ch for ch, is_unsub in results if is_unsub]
            return unsubscribed

    @staticmethod
    async def is_subscribed(bot: Bot, user_id: int) -> Tuple[bool, List[Channel]]:
        """Check if user is subscribed to all required channels."""
        unsubscribed = await SubscriptionService.get_unsubscribed_channels(bot, user_id)
        return len(unsubscribed) == 0, unsubscribed

    @staticmethod
    async def add_channel(channel_link: str, channel_name: str = None,
                          channel_id: str = None, channel_type: str = "telegram") -> Channel:
        """Add a new subscription channel"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            channel_repo = ChannelRepository(session)
            return await channel_repo.add_channel(
                channel_link=channel_link,
                channel_name=channel_name,
                channel_id=channel_id,
                channel_type=channel_type,
            )

    @staticmethod
    async def remove_channel(channel_db_id: int) -> bool:
        """Remove a subscription channel"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            channel_repo = ChannelRepository(session)
            return await channel_repo.remove_channel(channel_db_id)

    @staticmethod
    async def get_all_channels() -> List[Channel]:
        """Get all channels"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            channel_repo = ChannelRepository(session)
            return await channel_repo.get_all_channels()
    