"""
Idempotency Model for Preventing Duplicate Operations in Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and Stage 2 Phase 8 specifications.
Ensures at-most-once execution per tenant/key with deterministic response caching.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IdempotencyRecord(Base):
    """
    Idempotency key record guaranteeing single-execution semantics.
    Prevents double-spend, duplicate approval creation, or conflicting workflow starts.
    Stores cached HTTP response status and body for safe replay.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_idempotency_keys_tenant_key"),
        Index("ix_idempotency_keys_tenant_id", "tenant_id"),
        Index("ix_idempotency_keys_tenant_operation", "tenant_id", "operation"),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
        comment="Unique internal identifier for the idempotency record",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant within which this idempotency key is scoped",
    )
    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Client-supplied or caller-defined unique idempotency key",
    )
    operation: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Logical domain operation name (e.g. approval.create, payroll.execute)",
    )
    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 hexadecimal hash of the canonical request payload",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="started",
        server_default="started",
        nullable=False,
        comment="Execution state: started, completed, failed",
    )
    response_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Cached HTTP status code returned upon completion",
    )
    response_body: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Cached JSON response payload returned upon completion",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when operation began execution / row lock was acquired",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Optional TTL after which this idempotency record can be pruned",
    )

    @property
    def is_started(self) -> bool:
        return self.status == "started"

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"
