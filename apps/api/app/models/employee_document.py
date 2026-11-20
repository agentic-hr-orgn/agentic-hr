"""
Employee Document Model for Agentic HR Core (Stage 4).
Complies with ADR 002 (Multi-Tenancy with forced PostgreSQL RLS).
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmployeeDocument(Base):
    """
    Tenant-scoped employee document vault record (CNIC scans, employment contracts, certificates).
    Expiring documents are surfaced via `public.pub_documents_expiring` (security_invoker=true).
    """

    __tablename__ = "employee_documents"
    __table_args__ = (
        Index("ix_employee_documents_tenant_id", "tenant_id"),
        Index("ix_employee_documents_tenant_employee", "tenant_id", "employee_id"),
        Index("ix_employee_documents_tenant_expiry", "tenant_id", "expiry_date"),
        CheckConstraint(
            "document_type IN ('cnic', 'contract', 'certificate', 'other')",
            name="ck_employee_documents_document_type",
        ),
        CheckConstraint(
            "storage_provider IN ('local', 'google_drive')",
            name="ck_employee_documents_storage_provider",
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
        comment="Tenant scope for RLS isolation",
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
        nullable=False,
    )
    storage_provider: Mapped[str] = mapped_column(
        String(32),
        default="local",
        server_default="local",
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    web_view_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
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
