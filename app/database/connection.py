import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy import text

from app.config import config

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def get_engine() -> AsyncEngine:
    """Get or create the async database engine"""
    global _engine
    if _engine is None:
        db_url = config.db.url

        # asyncpg 'sslmode=require' ni tushunmaydi -> 'ssl=require' ga aylantirish
        if "postgresql" in db_url and "sslmode=" in db_url:
            db_url = db_url.replace("sslmode=require", "ssl=require")
            db_url = db_url.replace("sslmode=verify-full", "ssl=require")
            db_url = db_url.replace("sslmode=verify-ca", "ssl=require")

        _engine = create_async_engine(
            db_url,
            echo=config.log_level == "DEBUG",
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


async def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the session factory"""
    global _session_factory
    if _session_factory is None:
        engine = await get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session (async context manager)"""
    factory = await get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database - create all tables"""
    from app.database.models import Base

    engine = await get_engine()
    async with engine.begin() as conn:
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)

        # Migrations for existing databases
        # Check if channel_type column exists in channels table
        if "sqlite" in config.db.url:
            result = await conn.execute(text("PRAGMA table_info(channels)"))
            columns = [row[1] for row in result.fetchall()]
            if "channel_type" not in columns:
                await conn.execute(
                    text("ALTER TABLE channels ADD COLUMN channel_type VARCHAR DEFAULT 'telegram'")
                )
                logger.info("Migration: Added channel_type column to channels table")

    logger.info("Database initialized successfully")


async def close_db() -> None:
    """Close database connections"""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connections closed")
