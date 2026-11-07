"""
Approval model for human-in-the-loop decision workflows in Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy), ADR 003, and Stage 2 Phase 6 specifications.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.approval_revision import ApprovalRevision
    from app.models.tenant_entity import Tenant
    from app.models.user import User


class Approval(Base):
    """
    Approval request entity representing a human-in-the-loop governance gate.
    All records are multi-tenant isolated via PostgreSQL Row-Level Security (RLS)
    and strictly auditable with transactional state transitions.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_tenant_id", "tenant_id"),
        Index("ix_approvals_tenant_status", "tenant_id", "status"),
        Index("ix_approvals_requester_id", "requester_id"),
        Index("ix_approvals_created_at", "created_at"),
        Index("ix_approvals_workflow_id", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
        comment="Unique identifier for the approval request",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant within which this approval request was created",
    )
    approval_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Domain approval category: leave_request, candidate_offer, payroll_override, policy_exception",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Validated structured approval request details",
    )
    requester_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who submitted the approval request",
    )
    approver_role: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Required role or permission flag: owner, manager, hr_head, payroll_admin",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default="pending",
        nullable=False,
        comment="Approval lifecycle status: pending, approved, rejected",
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who committed the approval or rejection decision",
    )
    decision_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Mandatory reason when rejected; optional comment when approved",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
        comment="Precise UTC timestamp when request was submitted",
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Precise UTC timestamp when decision was committed",
    )

    current_revision: Mapped[int] = mapped_column(
        default=1,
        server_default="1",
        nullable=False,
        comment="Current active revision/round number (starts at 1)",
    )
    resubmitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Precise UTC timestamp when request was last resubmitted after changes requested",
    )
    workflow_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Optional Temporal workflow execution ID associated with this approval gate",
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    requester: Mapped["User | None"] = relationship("User", foreign_keys=[requester_id])
    decider: Mapped["User | None"] = relationship("User", foreign_keys=[decided_by])
    revisions: Mapped[list["ApprovalRevision"]] = relationship(
        "ApprovalRevision",
        back_populates="approval",
        cascade="all, delete-orphan",
        order_by="ApprovalRevision.revision_number.asc()",
    )
