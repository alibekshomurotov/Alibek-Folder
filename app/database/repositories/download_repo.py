"""Download Repository - Data access layer for Download model"""

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Download

logger = logging.getLogger(__name__)


class DownloadRepository:
    """Repository for Download model operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: int, platform: str, url: str,
                     quality: str = None, file_size: float = None) -> Download:
        """Record a new download"""
        download = Download(
            user_id=user_id,
            platform=platform,
            url=url,
            quality=quality,
            file_size=file_size,
        )
        self.session.add(download)
        await self.session.commit()
        await self.session.refresh(download)
        return download

    async def get_user_downloads(self, user_id: int, limit: int = 10) -> List[Download]:
        """Get user's recent downloads"""
        result = await self.session.execute(
            select(Download)
            .where(Download.user_id == user_id)
            .order_by(Download.downloaded_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_total_count(self) -> int:
        """Get total download count"""
        result = await self.session.execute(select(func.count(Download.id)))
        return result.scalar()

    async def get_today_count(self) -> int:
        """Get today's download count"""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(func.count(Download.id)).where(Download.downloaded_at >= today)
        )
        return result.scalar()

    async def get_total_size(self) -> float:
        """Get total downloaded size in MB"""
        result = await self.session.execute(
            select(func.sum(Download.file_size))
        )
        return result.scalar() or 0.0

    async def get_platform_stats(self) -> dict:
        """Get download count per platform"""
        result = await self.session.execute(
            select(Download.platform, func.count(Download.id))
            .group_by(Download.platform)
        )
        return dict(result.all())
