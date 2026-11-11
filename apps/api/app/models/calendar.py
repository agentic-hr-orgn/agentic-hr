"""
Google Calendar Event Metadata Model for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and ADR 006 (calendar.events scope least privilege).
Enforces default timezone: Asia/Karachi (PKT, UTC+05:00).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CalendarEventRecord(Base):
    """
    Metadata record for calendar events created or managed under calendar.events scope.
    Protected by PostgreSQL Row-Level Security (RLS).
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        Index("ix_calendar_events_tenant_id", "tenant_id"),
        Index("ix_calendar_events_google_event_id", "google_event_id"),
        Index("ix_calendar_events_tenant_start", "tenant_id", "start_time"),
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
    google_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hangout_link: Mapped[str | None] = mapped_column(String(512), nullable=True)

    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="Asia/Karachi",
        server_default="Asia/Karachi",
        nullable=False,
    )
    attendees: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=func.text("'[]'::jsonb"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="confirmed",
        server_default="confirmed",
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
