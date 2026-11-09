"""
Outbox Event Model for Transactional Messaging in Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and Stage 2 Phase 8 specifications.
Guarantees Dual-Write elimination by queueing external events within the business transaction.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutboxEvent(Base):
    """
    Transactional Outbox event record.
    Saved atomically within the same database transaction as business entity changes.
    Dispatched asynchronously by the Outbox Publisher Worker to external systems.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_tenant_id", "tenant_id"),
        Index("ix_outbox_events_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_outbox_events_created_at", "created_at"),
        Index("ix_outbox_events_event_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
        comment="Unique identifier for the outbox event",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant scope for RLS isolation",
    )
    event_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Domain event type identifier (e.g. approval.created, leave.approved)",
    )
    event_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Structured canonical event payload data",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default="pending",
        nullable=False,
        comment="Event lifecycle status: pending, processing, published, failed",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
        comment="Number of dispatch attempts",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when event was successfully published or permanently dead-lettered",
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
        comment="Next eligible execution time (used for exponential retry backoff)",
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Diagnostic error details / stack trace from the latest failure",
    )

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def is_processing(self) -> bool:
        return self.status == "processing"

    @property
    def is_published(self) -> bool:
        return self.status == "published"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"
