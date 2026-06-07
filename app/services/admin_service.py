"""Admin Service - Business logic for admin panel"""

import logging
from typing import List, Optional, Tuple

from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.download_repo import DownloadRepository
from app.database.repositories.channel_repo import ChannelRepository

logger = logging.getLogger(__name__)


class AdminService:
    """Service for admin panel operations"""

    @staticmethod
    async def get_stats() -> dict:
        """Get bot statistics"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            user_repo = UserRepository(session)
            download_repo = DownloadRepository(session)
            channel_repo = ChannelRepository(session)

            total_users = await user_repo.get_total_count()
            today_users = await user_repo.get_today_count()
            total_downloads = await download_repo.get_total_count()
            today_downloads = await download_repo.get_today_count()
            premium_count = await user_repo.get_premium_count()
            total_channels = await channel_repo.get_count()
            platform_stats = await download_repo.get_platform_stats()

            return {
                "total_users": total_users,
                "today_users": today_users,
                "total_downloads": total_downloads,
                "today_downloads": today_downloads,
                "premium_count": premium_count,
                "total_channels": total_channels,
                "platform_stats": platform_stats,
            }

    @staticmethod
    async def get_users(limit: int = 20, offset: int = 0) -> List:
        """Get users list"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            user_repo = UserRepository(session)
            return await user_repo.get_all_users(limit, offset)

    @staticmethod
    async def get_top_users(limit: int = 10) -> List:
        """Get top users by downloads"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            user_repo = UserRepository(session)
            return await user_repo.get_top_users(limit)

    @staticmethod
    async def ban_user(user_id: int, reason: str = None) -> bool:
        """Ban a user"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_id(user_id)
            if user:
                await user_repo.ban_user(user_id, reason)
                return True
            return False

    @staticmethod
    async def unban_user(user_id: int) -> bool:
        """Unban a user"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_id(user_id)
            if user:
                await user_repo.unban_user(user_id)
                return True
            return False

    @staticmethod
    async def search_users(query: str) -> List:
        """Search users"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            user_repo = UserRepository(session)
            return await user_repo.search_users(query)
