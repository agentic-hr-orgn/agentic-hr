"""
Tenant Isolation and Declarative Base Models for Agentic HR Core.
Complies with ADR 002 (One Postgres DB with Row-Level Security).
All multi-tenant entity models inherit from TenantBase.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenantBase(Base):
    """
    Abstract declarative base enforcing multi-tenant isolation.
    Every persistent entity table inherits tenant_id, created_at, and updated_at.
    """

    __abstract__ = True

    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="Tenant identifier for RLS isolation",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


async def set_tenant_context(
    session: AsyncSession, tenant_id: str, clerk_user_id: str | None = None
) -> None:
    """
    Sets the PostgreSQL transaction-local tenant and user context for Row-Level Security (RLS).
    Uses set_config(..., true) to ensure parameterization and transaction scoping (is_local=true).
    PostgreSQL policies enforce isolation using:
    `current_setting('app.tenant_id', true)` and `current_setting('app.clerk_user_id', true)`
    """
    if not tenant_id or not str(tenant_id).strip():
        raise ValueError("tenant_id must be a valid non-empty string.")
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id).strip()},
    )
    if clerk_user_id and str(clerk_user_id).strip():
        await session.execute(
            text("SELECT set_config('app.clerk_user_id', :clerk_user_id, true)"),
            {"clerk_user_id": str(clerk_user_id).strip()},
        )


async def set_clerk_user_context(session: AsyncSession, clerk_user_id: str) -> None:
    """Sets the transaction-local authenticated Clerk user ID context."""
    if not clerk_user_id or not str(clerk_user_id).strip():
        raise ValueError("clerk_user_id must be a valid non-empty string.")
    await session.execute(
        text("SELECT set_config('app.clerk_user_id', :clerk_user_id, true)"),
        {"clerk_user_id": str(clerk_user_id).strip()},
    )


async def get_tenant_context(session: AsyncSession) -> str | None:
    """Retrieves the current transaction-local tenant context from PostgreSQL."""
    result = await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
    val = result.scalar()
    return val if val else None


async def get_clerk_user_context(session: AsyncSession) -> str | None:
    """Retrieves the current transaction-local Clerk user ID context from PostgreSQL."""
    result = await session.execute(text("SELECT current_setting('app.clerk_user_id', true)"))
    val = result.scalar()
    return val if val else None


async def reset_tenant_context(session: AsyncSession) -> None:
    """Explicitly resets the tenant and user context."""
    await session.execute(text("RESET app.tenant_id; RESET app.clerk_user_id;"))
