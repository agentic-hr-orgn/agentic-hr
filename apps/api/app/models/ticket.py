"""
Helpdesk Ticket, Ticket Message, and Category Assignment Rule ORM Models (Stage 7).
All tables enforce Forced Row-Level Security (ADR 002) and ADR 010 invariants.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession


class Ticket(Base):
    """Tenant-scoped HR/IT/Facilities Helpdesk ticket with SLA deadlines and reopen tracking."""

    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticket_number", name="uq_tickets_tenant_ticket_number"),
        Index("ix_tickets_tenant_id", "tenant_id"),
        Index("ix_tickets_tenant_status", "tenant_id", "status"),
        Index("ix_tickets_tenant_requester", "tenant_id", "requester_user_id"),
        Index("ix_tickets_tenant_assignee", "tenant_id", "assigned_to_user_id"),
        Index("ix_tickets_tenant_sla_due", "tenant_id", "sla_due_at"),
        CheckConstraint(
            "category IN ('hr_policy', 'leave_attendance', 'payroll', 'it_access', 'facilities', 'general')",
            name="ck_tickets_category",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'urgent')",
            name="ck_tickets_priority",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'waiting_on_employee', 'resolved', 'closed')",
            name="ck_tickets_status",
        ),
        CheckConstraint(
            "source IN ('chat_abstain', 'chat_ask_hr', 'helpdesk_agent', 'manual')",
            name="ck_tickets_source",
        ),
        CheckConstraint("sla_reminder_at <= sla_due_at", name="ck_tickets_sla_order"),
        CheckConstraint(
            "satisfaction_rating IS NULL OR (satisfaction_rating >= 1 AND satisfaction_rating <= 5)",
            name="ck_tickets_satisfaction_rating",
        ),
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
    )
    ticket_number: Mapped[str] = mapped_column(Text, nullable=False)
    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(
        String(16),
        default="medium",
        server_default="medium",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="open",
        server_default="open",
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32),
        default="manual",
        server_default="manual",
        nullable=False,
    )
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sla_reminder_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sla_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sla_escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    satisfaction_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    satisfaction_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    source_session: Mapped["ChatSession | None"] = relationship(
        "ChatSession",
        foreign_keys=[source_session_id],
    )
    messages: Mapped[list["TicketMessage"]] = relationship(
        "TicketMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TicketMessage.created_at.asc()",
    )


class TicketMessage(Base):
    """Reply or HR-only internal note attached to a Helpdesk ticket."""

    __tablename__ = "ticket_messages"
    __table_args__ = (
        Index("ix_ticket_messages_tenant_id", "tenant_id"),
        Index(
            "ix_ticket_messages_tenant_ticket_created",
            "tenant_id",
            "ticket_id",
            "created_at",
        ),
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
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="messages")


class TicketAssignmentRule(Base):
    """Per-tenant category routing, SLA timing, and reopen window configuration."""

    __tablename__ = "ticket_assignment_rules"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "category",
            name="uq_ticket_assignment_rules_tenant_category",
        ),
        Index("ix_ticket_assignment_rules_tenant_id", "tenant_id"),
        CheckConstraint(
            "category IN ('hr_policy', 'leave_attendance', 'payroll', 'it_access', 'facilities', 'general')",
            name="ck_ticket_assignment_rules_category",
        ),
        CheckConstraint(
            "default_assignee_role IN ('hr_head', 'owner', 'manager')",
            name="ck_ticket_assignment_rules_assignee_role",
        ),
        CheckConstraint("sla_hours >= 1", name="ck_ticket_assignment_rules_sla_hours_pos"),
        CheckConstraint(
            "reminder_before_hours >= 0 AND reminder_before_hours < sla_hours",
            name="ck_ticket_assignment_rules_reminder_hours",
        ),
        CheckConstraint(
            "reopen_window_days >= 0",
            name="ck_ticket_assignment_rules_reopen_days",
        ),
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
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    default_assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_assignee_role: Mapped[str] = mapped_column(
        String(24),
        default="hr_head",
        server_default="hr_head",
        nullable=False,
    )
    sla_hours: Mapped[int] = mapped_column(
        Integer,
        default=24,
        server_default=text("24"),
        nullable=False,
    )
    reminder_before_hours: Mapped[int] = mapped_column(
        Integer,
        default=4,
        server_default=text("4"),
        nullable=False,
    )
    reopen_window_days: Mapped[int] = mapped_column(
        Integer,
        default=7,
        server_default=text("7"),
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
