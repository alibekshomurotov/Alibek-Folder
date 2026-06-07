"""Database Connection Manager - Supports SQLite (local) and PostgreSQL (Render/Neon)"""

import logging
from typing import AsyncGenerator
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

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


def _fix_database_url(url: str) -> tuple[str, dict]:
    """
    Fix DATABASE_URL for asyncpg compatibility.
    asyncpg doesn't support 'sslmode' query param - needs 'ssl' connect_arg instead.
    Returns (cleaned_url, connect_args)
    """
    connect_args = {}

    if "postgresql" not in url:
        # SQLite - no changes needed
        return url, connect_args

    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)

    # Handle sslmode -> ssl conversion for asyncpg
    if "sslmode" in query_params:
        ssl_value = query_params.pop("sslmode")[0]
        if ssl_value == "require":
            connect_args["ssl"] = "require"
        elif ssl_value == "disable":
            connect_args["ssl"] = False
        else:
            connect_args["ssl"] = ssl_value

    # Also handle ssl param directly
    if "ssl" in query_params:
        ssl_value = query_params.pop("ssl")[0]
        if ssl_value == "require" or ssl_value == "true":
            connect_args["ssl"] = "require"
        elif ssl_value == "disable" or ssl_value == "false":
            connect_args["ssl"] = False
        else:
            connect_args["ssl"] = ssl_value

    # Rebuild URL without sslmode/ssl params
    new_query = urlencode({k: v[0] for k, v in query_params.items()})
    cleaned_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment,
    ))

    return cleaned_url, connect_args


async def get_engine() -> AsyncEngine:
    """Get or create the async database engine"""
    global _engine
    if _engine is None:
        url = config.db.url
        kwargs = {
            "echo": config.log_level == "DEBUG",
        }

        if "sqlite" in url:
            kwargs["pool_pre_ping"] = True
        else:
            # PostgreSQL - fix URL for asyncpg
            cleaned_url, connect_args = _fix_database_url(url)
            url = cleaned_url
            kwargs["pool_pre_ping"] = True
            kwargs["pool_size"] = 5
            kwargs["max_overflow"] = 10
            if connect_args:
                kwargs["connect_args"] = connect_args

        _engine = create_async_engine(url, **kwargs)
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