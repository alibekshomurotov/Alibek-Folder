"""User Repository - Data access layer for User model"""

import logging
import random
import string
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User, Referral

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for User model operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: int) -> Optional[User]:
        """Get user by Telegram ID"""
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def create(self, user_id: int, username: str = None,
                     first_name: str = None, referred_by: int = None) -> User:
        """Create a new user"""
        referral_code = await self._generate_referral_code()
        user = User(
            id=user_id,
            username=username,
            first_name=first_name,
            referral_code=referral_code,
            referred_by=referred_by,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)

        # Log referral if applicable
        if referred_by:
            referral = Referral(
                referrer_id=referred_by,
                referred_id=user_id,
            )
            self.session.add(referral)
            referrer = await self.get_by_id(referred_by)
            if referrer:
                referrer.referrals_count += 1
            await self.session.commit()

        return user

    async def get_or_create(self, user_id: int, username: str = None,
                            first_name: str = None, referred_by: int = None) -> User:
        """Get user or create if doesn't exist"""
        user = await self.get_by_id(user_id)
        if user is None:
            user = await self.create(user_id, username, first_name, referred_by)
        else:
            # Update username and first_name if changed
            if username and user.username != username:
                user.username = username
            if first_name and user.first_name != first_name:
                user.first_name = first_name
            user.last_activity = datetime.now()
            await self.session.commit()
        return user

    async def update_download_count(self, user_id: int) -> None:
        """Increment user download count"""
        await self.session.execute(
            update(User).where(User.id == user_id).values(
                downloads_count=User.downloads_count + 1,
                last_activity=datetime.now(),
            )
        )
        await self.session.commit()

    async def ban_user(self, user_id: int, reason: str = None) -> None:
        """Ban a user"""
        await self.session.execute(
            update(User).where(User.id == user_id).values(
                is_banned=True,
                ban_reason=reason,
            )
        )
        await self.session.commit()

    async def unban_user(self, user_id: int) -> None:
        """Unban a user"""
        await self.session.execute(
            update(User).where(User.id == user_id).values(
                is_banned=False,
                ban_reason=None,
            )
        )
        await self.session.commit()

    async def get_all_users(self, limit: int = 100, offset: int = 0) -> List[User]:
        """Get all users with pagination"""
        result = await self.session.execute(
            select(User).order_by(User.registered_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def get_top_users(self, limit: int = 10) -> List[User]:
        """Get most active users by download count"""
        result = await self.session.execute(
            select(User).order_by(User.downloads_count.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_total_count(self) -> int:
        """Get total number of users"""
        result = await self.session.execute(select(func.count(User.id)))
        return result.scalar()

    async def get_today_count(self) -> int:
        """Get number of users registered today"""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(func.count(User.id)).where(User.registered_at >= today)
        )
        return result.scalar()

    async def search_users(self, query: str) -> List[User]:
        """Search users by ID or username"""
        result = await self.session.execute(
            select(User).where(
                (User.username.ilike(f"%{query}%")) |
                (User.first_name.ilike(f"%{query}%"))
            ).limit(20)
        )
        return list(result.scalars().all())

    async def _generate_referral_code(self) -> str:
        """Generate a unique referral code"""
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            result = await self.session.execute(
                select(User).where(User.referral_code == code)
            )
            if result.scalar_one_or_none() is None:
                return code
