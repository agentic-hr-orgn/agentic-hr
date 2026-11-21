"""
Policy Chunk Embedding Model for Agentic HR Core (Stage 5 Prompt 1).
Separate forced-RLS embedding table enabling zero-rewrite model upgrades (ADR 008).
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import PGVector

if TYPE_CHECKING:
    from app.models.policy_chunk import PolicyChunk


class PolicyChunkEmbedding(Base):
    """L2-normalized 768-dim vector embedding linked to a PolicyChunk."""

    __tablename__ = "policy_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id",
            "model",
            "dim",
            name="uq_policy_chunk_embeddings_chunk_model_dim",
        ),
        CheckConstraint("dim > 0", name="ck_policy_chunk_embeddings_dim"),
        Index("ix_policy_chunk_embeddings_tenant_id", "tenant_id"),
        Index(
            "ix_policy_chunk_embeddings_reuse_lookup",
            "tenant_id",
            "model",
            "dim",
            "content_hash",
        ),
        Index(
            "ix_policy_chunk_embeddings_hnsw_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
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
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policy_chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    dim: Mapped[int] = mapped_column(
        Integer,
        default=768,
        server_default=text("768"),
        nullable=False,
    )
    embedding: Mapped[list[float]] = mapped_column(PGVector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )

    chunk: Mapped["PolicyChunk"] = relationship(
        "PolicyChunk",
        back_populates="embeddings",
        foreign_keys=[chunk_id],
    )
