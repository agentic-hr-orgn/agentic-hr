"""
Audit Log model for immutable, tamper-evident tracking of business mutations.
Complies with ADR 002, ADR 003, and Stage 2 Phase 5 specifications.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.tenant_entity import Tenant
    from app.models.user import User


class AuditLog(Base):
    """
    Immutable audit log entry recording a specific mutation or event within a tenant.
    All entries are strictly append-only and protected by database triggers and RLS.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_id", "tenant_id"),
        Index("ix_audit_logs_actor_id", "actor_id"),
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_request_id", "request_id"),
        Index("ix_audit_logs_tenant_action", "tenant_id", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
        comment="Unique identifier for the audit log entry",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant within which this audited action occurred",
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who performed the action (NULL for automated system actions)",
    )
    action: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Operation name (e.g. membership.update, user.login, candidate.screen)",
    )
    target_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Entity type affected (e.g. membership, user, policy, candidate)",
    )
    target_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Identifier of the target entity affected",
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
        comment="Precise UTC timestamp when the action occurred",
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        comment="Sanitized JSON metadata describing the change (diffs, context)",
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="X-Request-ID correlating this audit log to an HTTP request",
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    actor: Mapped["User | None"] = relationship("User", foreign_keys=[actor_id])
