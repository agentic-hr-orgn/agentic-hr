"""
Approval Revision model for auditable version history and correction rounds in Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy), ADR 003, and Stage 2 approval correction specifications.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.approval import Approval
    from app.models.tenant_entity import Tenant
    from app.models.user import User


class ApprovalRevision(Base):
    """
    Append-only snapshot representing a specific revision round of an approval request.
    Stores the exact submitted payload snapshot, submitted timestamp, decision, reviewer feedback,
    and timestamps for complete auditability.
    """

    __tablename__ = "approval_revisions"
    __table_args__ = (
        Index("ix_approval_revisions_tenant_id", "tenant_id"),
        Index("ix_approval_revisions_approval_id", "approval_id"),
        Index("ix_approval_revisions_submitted_at", "submitted_at"),
        UniqueConstraint("approval_id", "revision_number", name="uq_approval_revision_round"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
        comment="Unique identifier for this approval revision snapshot",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant within which this revision exists",
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approvals.id", ondelete="CASCADE"),
        nullable=False,
        comment="Parent approval request",
    )
    revision_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Monotonically increasing revision number (1, 2, 3...)",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Snapshot of the domain payload submitted for this revision round",
    )
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who submitted this revision round",
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when this revision was submitted",
    )
    decision: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="Decision committed on this revision: pending, changes_requested, approved, rejected",
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who committed the decision on this revision",
    )
    decision_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Correction instructions (when changes_requested) or decision rationale",
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when decision was committed on this revision",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    approval: Mapped["Approval"] = relationship("Approval", back_populates="revisions")
    submitter: Mapped["User | None"] = relationship("User", foreign_keys=[submitted_by])
    decider: Mapped["User | None"] = relationship("User", foreign_keys=[decided_by])
