"""
Policy Chunk Model for Agentic HR Core (Stage 5 Prompt 1).
Stores section-aware policy chunks with bilingual FTS (`tsv`) and trigram (`normalized_text`)
search indices under forced PostgreSQL RLS.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.policy_chunk_embedding import PolicyChunkEmbedding
    from app.models.policy_document import PolicyDocument


class PolicyChunk(Base):
    """Tenant-scoped section chunk derived from a PolicyDocument."""

    __tablename__ = "policy_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_policy_chunks_doc_chunk_index",
        ),
        CheckConstraint("version >= 1", name="ck_policy_chunks_version"),
        CheckConstraint("chunk_index >= 0", name="ck_policy_chunks_chunk_index"),
        CheckConstraint("page_start >= 1", name="ck_policy_chunks_page_start"),
        CheckConstraint("page_end >= page_start", name="ck_policy_chunks_page_end"),
        CheckConstraint("token_count >= 0", name="ck_policy_chunks_token_count"),
        CheckConstraint(
            "visibility IN ('all', 'managers', 'hr_only')",
            name="ck_policy_chunks_visibility",
        ),
        Index("ix_policy_chunks_tenant_id", "tenant_id"),
        Index("ix_policy_chunks_tenant_doc_chunk", "tenant_id", "document_id", "chunk_index"),
        Index("ix_policy_chunks_tenant_content_hash", "tenant_id", "content_hash"),
        Index("ix_policy_chunks_tsv_gin", "tsv", postgresql_using="gin"),
        Index(
            "ix_policy_chunks_normalized_text_trgm",
            "normalized_text",
            postgresql_using="gin",
            postgresql_ops={"normalized_text": "gin_trgm_ops"},
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
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=sa_text("1"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    page_start: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=sa_text("1"),
        nullable=False,
    )
    page_end: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=sa_text("1"),
        nullable=False,
    )
    page_range: Mapped[str] = mapped_column(
        String(32),
        default="1",
        server_default="1",
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=sa_text("0"),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(
        String(16),
        default="unknown",
        server_default="unknown",
        nullable=False,
    )
    visibility: Mapped[str] = mapped_column(
        String(24),
        default="all",
        server_default="all",
        nullable=False,
    )
    tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)
    has_injection_flag: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=sa_text("false"),
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=sa_text("'{}'::jsonb"),
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
        back_populates="chunks",
        foreign_keys=[document_id],
    )
    embeddings: Mapped[list["PolicyChunkEmbedding"]] = relationship(
        "PolicyChunkEmbedding",
        back_populates="chunk",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
