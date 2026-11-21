"""
Policy Document Page Model for Agentic HR Core (Stage 5 Prompt 1).
Stores page-level extraction diagnostics, language, OCR fallback status, and cleaned text.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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
    from app.models.policy_document import PolicyDocument


class PolicyDocumentPage(Base):
    """Per-page extraction, quality score, and OCR state for an uploaded PolicyDocument."""

    __tablename__ = "policy_document_pages"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "page_number",
            name="uq_policy_document_pages_doc_page",
        ),
        CheckConstraint("page_number >= 1", name="ck_policy_document_pages_page_number"),
        CheckConstraint(
            "quality_score >= 0.0 AND quality_score <= 1.0",
            name="ck_policy_document_pages_quality_score",
        ),
        CheckConstraint(
            "extraction_status IN ('ok', 'needs_ocr', 'failed')",
            name="ck_policy_document_pages_extraction_status",
        ),
        Index("ix_policy_document_pages_tenant_id", "tenant_id"),
        Index(
            "ix_policy_document_pages_tenant_document",
            "tenant_id",
            "document_id",
            "page_number",
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
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policy_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    cleaned_text: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    language: Mapped[str] = mapped_column(
        String(16),
        default="unknown",
        server_default="unknown",
        nullable=False,
    )
    quality_score: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        server_default=text("0.0"),
        nullable=False,
    )
    needs_ocr: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    ocr_applied: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    extraction_status: Mapped[str] = mapped_column(
        String(24),
        default="ok",
        server_default="ok",
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
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

    document: Mapped["PolicyDocument"] = relationship(
        "PolicyDocument",
        back_populates="pages",
        foreign_keys=[document_id],
    )
