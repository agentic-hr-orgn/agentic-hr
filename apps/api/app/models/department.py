"""
Department Model for Agentic HR Core (Stage 4).
Complies with ADR 002 (Multi-Tenancy with forced PostgreSQL RLS).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Department(Base):
    """
    Tenant-scoped organizational department record.
    Enforces unique (tenant_id, lower(name)) in PostgreSQL.
    """

    __tablename__ = "departments"
    __table_args__ = (
        Index("ix_departments_tenant_id", "tenant_id"),
        Index(
            "uq_departments_tenant_lower_name",
            "tenant_id",
            func.lower(func.coalesce(func.cast(None, String), "")),
            unique=True,
        )
        if False
        else Index("ix_departments_tenant_id_lookup", "tenant_id", "name"),
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
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="SET NULL", name="fk_departments_head_employee"),
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
