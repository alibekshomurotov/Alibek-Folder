"""PromoCode Repository - Data access layer for PromoCode model"""

import logging
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PromoCode

logger = logging.getLogger(__name__)


class PromoCodeRepository:
    """Repository for PromoCode model operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, code: str, premium_days: int, max_uses: int = 1) -> PromoCode:
        """Create a new promo code"""
        promo = PromoCode(
            code=code,
            premium_days=premium_days,
            max_uses=max_uses,
        )
        self.session.add(promo)
        await self.session.commit()
        await self.session.refresh(promo)
        return promo

    async def get_by_code(self, code: str) -> Optional[PromoCode]:
        """Get promo code by its code string"""
        result = await self.session.execute(
            select(PromoCode).where(PromoCode.code == code)
        )
        return result.scalar_one_or_none()

    async def use_code(self, code: str) -> Optional[PromoCode]:
        """Use a promo code (increment usage count)"""
        promo = await self.get_by_code(code)
        if promo and promo.is_active and promo.used_count < promo.max_uses:
            promo.used_count += 1
            if promo.used_count >= promo.max_uses:
                promo.is_active = False
            await self.session.commit()
            await self.session.refresh(promo)
            return promo
        return None

    async def deactivate(self, code: str) -> bool:
        """Deactivate a promo code"""
        result = await self.session.execute(
            update(PromoCode)
            .where(PromoCode.code == code)
            .values(is_active=False)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def get_all(self):
        """Get all promo codes"""
        result = await self.session.execute(
            select(PromoCode).order_by(PromoCode.created_at.desc())
        )
        return list(result.scalars().all())
