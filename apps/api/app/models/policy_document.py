"""
Policy Document Model for Agentic HR Core (Stage 4 + Stage 5 RAG Foundation).
Complies with ADR 002 (Multi-Tenancy with forced PostgreSQL RLS) & ADR 008.
"""

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.approval import Approval
    from app.models.policy_chunk import PolicyChunk
    from app.models.policy_document_page import PolicyDocumentPage


class PolicyDocument(Base):
    """
    Tenant-scoped corporate policy document metadata record with dual-lifecycle state:
    - `ingestion_status`: pipeline state (`queued` | `processing` | `needs_review` | `indexed` | `failed`)
    - `lifecycle_status`: governance state (`draft` | `review` | `active` | `superseded` | `archived`)
    """

    __tablename__ = "policy_documents"
    __table_args__ = (
        CheckConstraint(
            "doc_type IN ('company_policy', 'labour_law', 'helpdesk_knowledge')",
            name="ck_policy_documents_doc_type",
        ),
        CheckConstraint(
            "visibility IN ('all', 'managers', 'hr_only')",
            name="ck_policy_documents_visibility",
        ),
        CheckConstraint("version >= 1", name="ck_policy_documents_version"),
        CheckConstraint(
            "ingestion_status IN "
            "('queued_stub', 'queued', 'processing', 'needs_review', 'indexed', 'failed')",
            name="ck_policy_documents_ingestion_status",
        ),
        CheckConstraint(
            "lifecycle_status IN ('draft', 'review', 'active', 'superseded', 'archived')",
            name="ck_policy_documents_lifecycle_status",
        ),
        CheckConstraint("page_count >= 0", name="ck_policy_documents_page_count"),
        CheckConstraint(
            "progress_pct >= 0 AND progress_pct <= 100",
            name="ck_policy_documents_progress_pct",
        ),
        Index("ix_policy_documents_tenant_id", "tenant_id"),
        Index(
            "uq_policy_documents_tenant_lower_title_version",
            "tenant_id",
            text("lower(title)"),
            "version",
            unique=True,
        ),
        Index("ix_policy_documents_tenant_lifecycle", "tenant_id", "lifecycle_status"),
        Index("ix_policy_documents_tenant_ingestion", "tenant_id", "ingestion_status"),
        Index("ix_policy_documents_tenant_doc_type", "tenant_id", "doc_type", "visibility"),
        Index("ix_policy_documents_approval_id", "approval_id"),
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
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(
        String(32),
        default="company_policy",
        server_default="company_policy",
        nullable=False,
    )
    visibility: Mapped[str] = mapped_column(
        String(24),
        default="all",
        server_default="all",
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )
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
        default="queued",
        server_default="queued",
        nullable=False,
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(24),
        default="draft",
        server_default="draft",
        nullable=False,
    )
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(
        String(100),
        default="upload",
        server_default="upload",
        nullable=False,
    )
    supersedes_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policy_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approvals.id", ondelete="SET NULL"),
        nullable=True,
    )
    page_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    progress_pct: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    progress_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    low_confidence_pages: Mapped[list[Any]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    has_prompt_injection_warning: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
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

    pages: Mapped[list["PolicyDocumentPage"]] = relationship(
        "PolicyDocumentPage",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PolicyDocumentPage.page_number.asc()",
    )
    chunks: Mapped[list["PolicyChunk"]] = relationship(
        "PolicyChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PolicyChunk.chunk_index.asc()",
    )
    supersedes_document: Mapped["PolicyDocument | None"] = relationship(
        "PolicyDocument",
        remote_side=[id],
        foreign_keys=[supersedes_document_id],
    )
    approval: Mapped["Approval | None"] = relationship(
        "Approval",
        foreign_keys=[approval_id],
    )
