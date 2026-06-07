"""Premium Service - Business logic for premium features"""

import logging
from typing import Optional

from app.database.connection import get_session_factory
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.promo_repo import PromoCodeRepository
from app.database.models import PromoCode

logger = logging.getLogger(__name__)


class PremiumService:
    """Service for premium-related operations"""

    @staticmethod
    async def is_premium(user_id: int) -> bool:
        """Check if user has active premium"""
        session_factory = await get_session_factory()

        async with session_factory() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_id(user_id)
            return user.is_premium_active if user else False

    @staticmethod
    async def grant_premium(user_id: int, days: int, reason: str = "admin_grant") -> bool:
        """Grant premium to user"""
        try:
            session_factory = await get_session_factory()
            async with session_factory() as session:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_id(user_id)
                if user:
                    await user_repo.grant_premium(user_id, days, reason)
                    return True
            return False
        except Exception as e:
            logger.error(f"Error granting premium: {e}")
            return False

    @staticmethod
    async def revoke_premium(user_id: int) -> bool:
        """Revoke premium from user"""
        try:
            session_factory = await get_session_factory()
            async with session_factory() as session:
                user_repo = UserRepository(session)
                user = await user_repo.get_by_id(user_id)
                if user:
                    await user_repo.revoke_premium(user_id)
                    return True
            return False
        except Exception as e:
            logger.error(f"Error revoking premium: {e}")
            return False

    @staticmethod
    async def redeem_promo_code(user_id: int, code: str) -> Optional[PromoCode]:
        """Redeem a promo code for premium"""
        try:
            session_factory = await get_session_factory()
            async with session_factory() as session:
                promo_repo = PromoCodeRepository(session)
                user_repo = UserRepository(session)

                promo = await promo_repo.use_code(code)
                if promo:
                    await user_repo.grant_premium(user_id, promo.premium_days, "promo_code")
                    return promo
                return None
        except Exception as e:
            logger.error(f"Error redeeming promo code: {e}")
            return None

    @staticmethod
    async def create_promo_code(premium_days: int, max_uses: int = 1) -> PromoCode:
        """Create a new promo code"""
        from app.utils.helpers import generate_promo_code

        session_factory = await get_session_factory()
        async with session_factory() as session:
            promo_repo = PromoCodeRepository(session)
            code = generate_promo_code()
            return await promo_repo.create(code, premium_days, max_uses)
