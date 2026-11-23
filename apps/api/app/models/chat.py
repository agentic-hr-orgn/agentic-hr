"""
Policy Brain Chat Session, Message, and Answer Feedback ORM Models (Stage 7).
All tables enforce Forced Row-Level Security (ADR 002) and ADR 010 invariants.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.ticket import Ticket


class ChatSession(Base):
    """Tenant-scoped bilingual conversational session for Policy Brain & Helpdesk."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_tenant_id", "tenant_id"),
        Index("ix_chat_sessions_tenant_user_last_msg", "tenant_id", "user_id", "last_message_at"),
        CheckConstraint(
            "language IN ('en', 'ur', 'roman_ur', 'mixed')",
            name="ck_chat_sessions_language",
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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        default="New conversation",
        server_default="New conversation",
        nullable=False,
    )
    language: Mapped[str] = mapped_column(
        String(16),
        default="en",
        server_default="en",
        nullable=False,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.created_at.asc()",
    )


class ChatMessage(Base):
    """Individual turn in a Policy Brain / Helpdesk chat session with citations and telemetry."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_tenant_id", "tenant_id"),
        Index(
            "ix_chat_messages_tenant_session_created",
            "tenant_id",
            "session_id",
            "created_at",
        ),
        Index("ix_chat_messages_ticket_id", "ticket_id"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_chat_messages_role"),
        CheckConstraint(
            "language IN ('en', 'ur', 'roman_ur', 'mixed')",
            name="ck_chat_messages_language",
        ),
        CheckConstraint(
            "jsonb_typeof(citations) = 'array'",
            name="ck_chat_messages_citations_array",
        ),
        CheckConstraint(
            "jsonb_typeof(tool_calls) = 'array'",
            name="ck_chat_messages_tool_calls_array",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_chat_messages_latency_non_neg",
        ),
        CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="ck_chat_messages_cost_non_neg",
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
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(
        String(16),
        default="en",
        server_default="en",
        nullable=False,
    )
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    abstained: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="SET NULL"),
        nullable=True,
    )
    handoff_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    topic_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")
    ticket: Mapped["Ticket | None"] = relationship("Ticket", foreign_keys=[ticket_id])
    feedback_items: Mapped[list["AnswerFeedback"]] = relationship(
        "AnswerFeedback",
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AnswerFeedback(Base):
    """Per-user thumbs up/down feedback on assistant answers for RAG quality tracking."""

    __tablename__ = "answer_feedback"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "message_id",
            "user_id",
            name="uq_answer_feedback_tenant_msg_user",
        ),
        Index("ix_answer_feedback_tenant_id", "tenant_id"),
        Index("ix_answer_feedback_tenant_message", "tenant_id", "message_id"),
        CheckConstraint("rating IN ('up', 'down')", name="ck_answer_feedback_rating"),
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
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )

    message: Mapped["ChatMessage"] = relationship("ChatMessage", back_populates="feedback_items")
