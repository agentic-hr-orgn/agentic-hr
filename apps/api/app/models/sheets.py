"""
Google Sheets Synchronization Queue and Drift Logs Models for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy), ADR 005 (Postgres is Authoritative, Sheets is Synced View),
and ADR 006 (Google Workspace Least Privilege).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SheetsSyncQueue(Base):
    """
    Asynchronous queue item for one-way synchronization from PostgreSQL to Google Sheets.
    Protected by PostgreSQL Row-Level Security (RLS).
    """

    __tablename__ = "sheets_sync_queue"
    __table_args__ = (
        Index("ix_sheets_sync_queue_tenant_id", "tenant_id"),
        Index("ix_sheets_sync_queue_status_scheduled_at", "status", "scheduled_at"),
        Index("ix_sheets_sync_queue_entity", "tenant_id", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant scope for RLS isolation",
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connector_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    spreadsheet_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sheet_name: Mapped[str] = mapped_column(
        String(100), default="Employees", server_default="Employees", nullable=False
    )
    direction: Mapped[str] = mapped_column(
        String(50),
        default="postgres_to_sheets",
        server_default="postgres_to_sheets",
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # 'upsert', 'delete'
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )  # 'pending', 'processing', 'completed', 'failed'
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class SheetsDriftLog(Base):
    """
    Immutable audit record for detected discrepancies between PostgreSQL and Google Sheets.
    Reconciled by re-asserting PostgreSQL authoritative state over drifted Sheet cells.
    Protected by PostgreSQL Row-Level Security (RLS).
    """

    __tablename__ = "sheets_drift_logs"
    __table_args__ = (
        Index("ix_sheets_drift_logs_tenant_id", "tenant_id"),
        Index("ix_sheets_drift_logs_entity", "tenant_id", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant scope for RLS isolation",
    )
    spreadsheet_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sheet_name: Mapped[str] = mapped_column(
        String(100), default="Employees", server_default="Employees", nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    postgres_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    sheets_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution: Mapped[str] = mapped_column(
        String(50),
        default="postgres_overwrote_sheets",
        server_default="postgres_overwrote_sheets",
        nullable=False,
    )  # 'postgres_overwrote_sheets', 'flagged_for_human_review'
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
