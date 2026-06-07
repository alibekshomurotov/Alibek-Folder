"""SQLAlchemy Database Models"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, DateTime, Float,
    ForeignKey, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models"""
    pass


class User(Base):
    """User model"""
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)  # Telegram user ID
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    registered_at = Column(DateTime, default=func.now())
    downloads_count = Column(Integer, default=0)
    is_premium = Column(Boolean, default=False)
    premium_until = Column(DateTime, nullable=True)
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(String(500), nullable=True)
    referred_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    referral_code = Column(String(20), unique=True, nullable=True)
    referrals_count = Column(Integer, default=0)
    last_activity = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    downloads = relationship("Download", back_populates="user", lazy="selectin")
    referrals = relationship("Referral", back_populates="referrer", lazy="selectin")

    @property
    def is_premium_active(self) -> bool:
        if not self.is_premium:
            return False
        if self.premium_until and self.premium_until < datetime.now():
            return False
        return True

    @property
    def referral_link(self) -> str:
        from app.config import config
        return f"https://t.me/{config.bot.username}?start=ref_{self.referral_code}"


class Channel(Base):
    """Channel/subscription model"""
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(255), nullable=True)  # Telegram channel ID (for Telegram type)
    channel_link = Column(String(500), nullable=False)
    channel_name = Column(String(255), nullable=True)
    channel_type = Column(String(50), default="telegram")  # telegram, instagram, youtube, etc.
    added_at = Column(DateTime, default=func.now())
    is_active = Column(Boolean, default=True)


class Download(Base):
    """Download history model"""
    __tablename__ = "downloads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    platform = Column(String(50), nullable=True)
    url = Column(Text, nullable=False)
    quality = Column(String(10), nullable=True)
    file_size = Column(Float, nullable=True)  # in MB
    downloaded_at = Column(DateTime, default=func.now())

    # Relationships
    user = relationship("User", back_populates="downloads")


class PromoCode(Base):
    """Promo code model"""
    __tablename__ = "promo_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False)
    premium_days = Column(Integer, nullable=False)
    max_uses = Column(Integer, default=1)
    used_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    is_active = Column(Boolean, default=True)


class PremiumLog(Base):
    """Premium action log model"""
    __tablename__ = "premium_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    days = Column(Integer, nullable=False)
    reason = Column(String(50), nullable=False)  # purchase, referral, promo_code, admin_grant
    created_at = Column(DateTime, default=func.now())


class Referral(Base):
    """Referral tracking model"""
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    referrer_id = Column(BigInteger, ForeignKey("users.id"))
    referred_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, default=func.now())

    # Relationships
    referrer = relationship("User", back_populates="referrals")
