"""
Policy Document Model for Agentic HR Core (Stage 4).
Complies with ADR 002 (Multi-Tenancy with forced PostgreSQL RLS).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PolicyDocument(Base):
    """
    Tenant-scoped corporate policy document metadata record (Stage 4 onboarding upload & Stage 7 RAG).
    """

    __tablename__ = "policy_documents"
    __table_args__ = (Index("ix_policy_documents_tenant_id", "tenant_id"),)

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
    ingestion_status: Mapped[str] = mapped_column(
        String(32),
        default="queued_stub",
        server_default="queued_stub",
        nullable=False,
    )
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
