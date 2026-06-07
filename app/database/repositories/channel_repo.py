"""Channel Repository - Data access layer for Channel model"""

import logging
from typing import List, Optional

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Channel

logger = logging.getLogger(__name__)


class ChannelRepository:
    """Repository for Channel model operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_channel(self, channel_link: str, channel_name: str = None,
                          channel_id: str = None, channel_type: str = "telegram") -> Channel:
        """Add a new subscription channel"""
        channel = Channel(
            channel_id=channel_id,
            channel_link=channel_link,
            channel_name=channel_name,
            channel_type=channel_type,
        )
        self.session.add(channel)
        await self.session.commit()
        await self.session.refresh(channel)
        return channel

    async def remove_channel(self, channel_id: int) -> bool:
        """Remove a channel by database ID"""
        result = await self.session.execute(
            delete(Channel).where(Channel.id == channel_id)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def get_all_channels(self, active_only: bool = True) -> List[Channel]:
        """Get all channels"""
        query = select(Channel)
        if active_only:
            query = query.where(Channel.is_active == True)
        result = await self.session.execute(query.order_by(Channel.added_at.desc()))
        return list(result.scalars().all())

    async def get_telegram_channels(self) -> List[Channel]:
        """Get only Telegram channels (for subscription verification)"""
        result = await self.session.execute(
            select(Channel).where(
                Channel.channel_type == "telegram",
                Channel.is_active == True,
            )
        )
        return list(result.scalars().all())

    async def get_by_id(self, channel_id: int) -> Optional[Channel]:
        """Get channel by database ID"""
        result = await self.session.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        return result.scalar_one_or_none()

    async def toggle_active(self, channel_id: int) -> bool:
        """Toggle channel active status"""
        channel = await self.get_by_id(channel_id)
        if channel:
            channel.is_active = not channel.is_active
            await self.session.commit()
            return channel.is_active
        return False

    async def get_count(self) -> int:
        """Get total channel count"""
        from sqlalchemy import func
        result = await self.session.execute(
            select(func.count(Channel.id))
        )
        return result.scalar()
