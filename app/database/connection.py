"""Database Connection Manager - Supports SQLite (local) and PostgreSQL (Render/Neon)"""

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


def _get_engine_kwargs() -> dict:
    """Get engine kwargs based on database type"""
    url = config.db.url
    kwargs = {
        "echo": config.log_level == "DEBUG",
    }

    if "sqlite" in url:
        # SQLite settings (local development)
        kwargs["pool_pre_ping"] = True
    else:
        # PostgreSQL settings (Neon/production)
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10

        # Neon PostgreSQL requires SSL
        if "neon.tech" in url or "sslmode" not in url:
            connect_args = kwargs.get("connect_args", {})
            connect_args["ssl"] = "require"
            kwargs["connect_args"] = connect_args

    return kwargs


async def get_engine() -> AsyncEngine:
    """Get or create the async database engine"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            config.db.url,
            **_get_engine_kwargs()
        )
        logger.info(f"Database engine created: {'PostgreSQL' if 'postgresql' in config.db.url else 'SQLite'}")
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

        # Migrations for SQLite databases
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
