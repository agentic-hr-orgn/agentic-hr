"""
SQLAlchemy 2.0 Asynchronous Database Session Management for Agentic HR Core.
Enforces a consistent asyncpg connection pipeline across all runtime queries and migrations.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger("agentic_hr.db")

# Asynchronous engine using asyncpg driver
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# AsyncSession factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding a scoped asynchronous database session.

    INVARIANT (Stage 3+ / Stage 4+):
    External connector calls must NEVER execute mid-transaction after uncommitted domain writes
    (Stage 4+ workflows must dispatch external side effects via Outbox/Temporal).
    Because of this invariant, committing on ConnectorAuthExpiredError safely persists only
    the 401 circuit-breaker trip (needs_reconnect, jobs_paused=True) and immutable audit row
    even if a route forgets an explicit `except ConnectorAuthExpiredError: await db.commit()`.
    """
    from app.services.connector_service import ConnectorAuthExpiredError

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except ConnectorAuthExpiredError:
            await session.commit()
            raise
        finally:
            await session.close()


# Common alias for FastAPI dependency injection
get_db = get_db_session


async def check_db_health() -> dict[str, bool]:
    """
    Verifies database connectivity and checks for pgvector extension presence.
    Returns status dictionary: {'connected': bool, 'pgvector_enabled': bool}.
    """
    status_info = {"connected": False, "pgvector_enabled": False}

    try:
        async with asyncio.timeout(2.0):
            async with engine.connect() as conn:
                # 1. Connectivity check
                await conn.execute(text("SELECT 1"))
                status_info["connected"] = True

                # 2. pgvector extension check
                result = await conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'vector';")
                )
                if result.scalar() is not None:
                    status_info["pgvector_enabled"] = True
    except Exception as exc:
        logger.warning(f"Database health check failed: {exc}")

    return status_info
