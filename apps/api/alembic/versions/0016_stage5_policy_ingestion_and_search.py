"""stage 5 prompt 1: pg_trgm, policy ingestion tables, HNSW embeddings & dual-lifecycle schema

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-21 23:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import (
    create_tenant_rls_policies,
    disable_rls,
    drop_tenant_rls_policies,
    enable_rls,
)
from app.db.types import PGVector

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE5_NEW_TABLES: tuple[str, ...] = (
    "policy_document_pages",
    "policy_chunks",
    "policy_chunk_embeddings",
)


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Enable pg_trgm extension for bilingual/fuzzy retrieval
    # ---------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # ---------------------------------------------------------
    # 2. Evolve public.policy_documents (preserving Stage 4 rows & compatibility)
    # ---------------------------------------------------------
    op.add_column(
        "policy_documents",
        sa.Column(
            "doc_type",
            sa.String(length=32),
            server_default="company_policy",
            nullable=False,
        ),
    )
    op.add_column(
        "policy_documents",
        sa.Column(
            "visibility",
            sa.String(length=24),
            server_default="all",
            nullable=False,
        ),
    )
    op.add_column(
        "policy_documents",
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "policy_documents",
        sa.Column(
            "lifecycle_status",
            sa.String(length=24),
            server_default="draft",
            nullable=False,
        ),
    )
    op.add_column(
        "policy_documents",
        sa.Column("effective_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "policy_documents",
        sa.Column(
            "source",
            sa.String(length=100),
            server_default="upload",
            nullable=False,
        ),
    )
    op.add_column(
        "policy_documents",
        sa.Column("supersedes_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "policy_documents",
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "policy_documents",
        sa.Column(
            "page_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "policy_documents",
        sa.Column(
            "progress_pct",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "policy_documents",
        sa.Column("progress_stage", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "policy_documents",
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "policy_documents",
        sa.Column(
            "low_confidence_pages",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "policy_documents",
        sa.Column(
            "has_prompt_injection_warning",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    op.alter_column(
        "policy_documents",
        "ingestion_status",
        existing_type=sa.String(length=32),
        server_default="queued",
        existing_nullable=False,
    )

    op.create_foreign_key(
        "fk_policy_documents_supersedes_document",
        "policy_documents",
        "policy_documents",
        ["supersedes_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_policy_documents_approval",
        "policy_documents",
        "approvals",
        ["approval_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_check_constraint(
        "ck_policy_documents_doc_type",
        "policy_documents",
        "doc_type IN ('company_policy', 'labour_law', 'helpdesk_knowledge')",
    )
    op.create_check_constraint(
        "ck_policy_documents_visibility",
        "policy_documents",
        "visibility IN ('all', 'managers', 'hr_only')",
    )
    op.create_check_constraint(
        "ck_policy_documents_version",
        "policy_documents",
        "version >= 1",
    )
    op.create_check_constraint(
        "ck_policy_documents_ingestion_status",
        "policy_documents",
        "ingestion_status IN "
        "('queued_stub', 'queued', 'processing', 'needs_review', 'indexed', 'failed')",
    )
    op.create_check_constraint(
        "ck_policy_documents_lifecycle_status",
        "policy_documents",
        "lifecycle_status IN ('draft', 'review', 'active', 'superseded', 'archived')",
    )
    op.create_check_constraint(
        "ck_policy_documents_page_count",
        "policy_documents",
        "page_count >= 0",
    )
    op.create_check_constraint(
        "ck_policy_documents_progress_pct",
        "policy_documents",
        "progress_pct >= 0 AND progress_pct <= 100",
    )

    # Ensure any pre-existing duplicate titles per tenant get distinct version numbers
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY tenant_id, lower(title)
                ORDER BY created_at ASC, id ASC
            ) AS rn
            FROM public.policy_documents
        )
        UPDATE public.policy_documents p
        SET version = ranked.rn
        FROM ranked
        WHERE p.id = ranked.id AND ranked.rn > 1;
        """
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_policy_documents_tenant_lower_title_version "
        "ON public.policy_documents (tenant_id, lower(title), version);"
    )
    op.create_index(
        "ix_policy_documents_tenant_lifecycle",
        "policy_documents",
        ["tenant_id", "lifecycle_status"],
    )
    op.create_index(
        "ix_policy_documents_tenant_ingestion",
        "policy_documents",
        ["tenant_id", "ingestion_status"],
    )
    op.create_index(
        "ix_policy_documents_tenant_doc_type",
        "policy_documents",
        ["tenant_id", "doc_type", "visibility"],
    )
    op.create_index(
        "ix_policy_documents_approval_id",
        "policy_documents",
        ["approval_id"],
    )

    # ---------------------------------------------------------
    # 3. Create public.policy_document_pages
    # ---------------------------------------------------------
    op.create_table(
        "policy_document_pages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policy_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), server_default="", nullable=False),
        sa.Column("cleaned_text", sa.Text(), server_default="", nullable=False),
        sa.Column("language", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column(
            "quality_score",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "needs_ocr",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "ocr_applied",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "extraction_status",
            sa.String(length=24),
            server_default="ok",
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "document_id",
            "page_number",
            name="uq_policy_document_pages_doc_page",
        ),
        sa.CheckConstraint("page_number >= 1", name="ck_policy_document_pages_page_number"),
        sa.CheckConstraint(
            "quality_score >= 0.0 AND quality_score <= 1.0",
            name="ck_policy_document_pages_quality_score",
        ),
        sa.CheckConstraint(
            "extraction_status IN ('ok', 'needs_ocr', 'failed')",
            name="ck_policy_document_pages_extraction_status",
        ),
    )
    op.create_index("ix_policy_document_pages_tenant_id", "policy_document_pages", ["tenant_id"])
    op.create_index(
        "ix_policy_document_pages_tenant_document",
        "policy_document_pages",
        ["tenant_id", "document_id", "page_number"],
    )

    # ---------------------------------------------------------
    # 4. Create public.policy_chunks
    # ---------------------------------------------------------
    op.create_table(
        "policy_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policy_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_path", sa.Text(), server_default="", nullable=False),
        sa.Column("page_start", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("page_end", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("page_range", sa.String(length=32), server_default="1", nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("language", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column("visibility", sa.String(length=24), server_default="all", nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "has_injection_flag",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_policy_chunks_doc_chunk_index",
        ),
        sa.CheckConstraint("version >= 1", name="ck_policy_chunks_version"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_policy_chunks_chunk_index"),
        sa.CheckConstraint("page_start >= 1", name="ck_policy_chunks_page_start"),
        sa.CheckConstraint("page_end >= page_start", name="ck_policy_chunks_page_end"),
        sa.CheckConstraint("token_count >= 0", name="ck_policy_chunks_token_count"),
        sa.CheckConstraint(
            "visibility IN ('all', 'managers', 'hr_only')",
            name="ck_policy_chunks_visibility",
        ),
    )
    op.create_index("ix_policy_chunks_tenant_id", "policy_chunks", ["tenant_id"])
    op.create_index(
        "ix_policy_chunks_tenant_doc_chunk",
        "policy_chunks",
        ["tenant_id", "document_id", "chunk_index"],
    )
    op.create_index(
        "ix_policy_chunks_tenant_content_hash",
        "policy_chunks",
        ["tenant_id", "content_hash"],
    )
    op.execute("CREATE INDEX ix_policy_chunks_tsv_gin ON public.policy_chunks USING gin (tsv);")
    op.execute(
        "CREATE INDEX ix_policy_chunks_normalized_text_trgm "
        "ON public.policy_chunks USING gin (normalized_text gin_trgm_ops);"
    )

    # ---------------------------------------------------------
    # 5. Create public.policy_chunk_embeddings (separate table for zero-rewrite upgrades)
    # ---------------------------------------------------------
    op.create_table(
        "policy_chunk_embeddings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policy_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("dim", sa.Integer(), server_default=sa.text("768"), nullable=False),
        sa.Column("embedding", PGVector(768), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "chunk_id",
            "model",
            "dim",
            name="uq_policy_chunk_embeddings_chunk_model_dim",
        ),
        sa.CheckConstraint("dim > 0", name="ck_policy_chunk_embeddings_dim"),
    )
    op.create_index(
        "ix_policy_chunk_embeddings_tenant_id",
        "policy_chunk_embeddings",
        ["tenant_id"],
    )
    op.create_index(
        "ix_policy_chunk_embeddings_reuse_lookup",
        "policy_chunk_embeddings",
        ["tenant_id", "model", "dim", "content_hash"],
    )
    op.execute(
        "CREATE INDEX ix_policy_chunk_embeddings_hnsw_cosine "
        "ON public.policy_chunk_embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);"
    )

    # ---------------------------------------------------------
    # 6. Enforce Forced RLS, Ownership & Grants on all 3 new tables
    # ---------------------------------------------------------
    for tbl in STAGE5_NEW_TABLES:
        qualified = f"public.{tbl}"
        enable_rls(qualified)
        create_tenant_rls_policies(qualified)
        op.execute(f"ALTER TABLE {qualified} OWNER TO agentic_hr_migrator;")
        op.execute(f"GRANT ALL ON TABLE {qualified} TO agentic_hr_migrator;")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {qualified} TO agentic_hr_app;")
        op.execute(f"REVOKE TRUNCATE ON TABLE {qualified} FROM agentic_hr_app;")


def downgrade() -> None:
    # 1. Drop RLS policies, disable RLS, and drop Stage 5 tables in reverse dependency order
    for tbl in reversed(STAGE5_NEW_TABLES):
        qualified = f"public.{tbl}"
        drop_tenant_rls_policies(qualified)
        disable_rls(qualified)
        op.drop_table(tbl)

    # 2. Revert policy_documents indexes, constraints, FKs, and added columns
    op.drop_index("ix_policy_documents_approval_id", table_name="policy_documents")
    op.drop_index("ix_policy_documents_tenant_doc_type", table_name="policy_documents")
    op.drop_index("ix_policy_documents_tenant_ingestion", table_name="policy_documents")
    op.drop_index("ix_policy_documents_tenant_lifecycle", table_name="policy_documents")
    op.execute("DROP INDEX IF EXISTS public.uq_policy_documents_tenant_lower_title_version;")

    for ck_name in (
        "ck_policy_documents_progress_pct",
        "ck_policy_documents_page_count",
        "ck_policy_documents_lifecycle_status",
        "ck_policy_documents_ingestion_status",
        "ck_policy_documents_version",
        "ck_policy_documents_visibility",
        "ck_policy_documents_doc_type",
    ):
        op.drop_constraint(ck_name, "policy_documents", type_="check")

    op.drop_constraint("fk_policy_documents_approval", "policy_documents", type_="foreignkey")
    op.drop_constraint(
        "fk_policy_documents_supersedes_document",
        "policy_documents",
        type_="foreignkey",
    )

    op.alter_column(
        "policy_documents",
        "ingestion_status",
        existing_type=sa.String(length=32),
        server_default="queued_stub",
        existing_nullable=False,
    )

    for col_name in (
        "has_prompt_injection_warning",
        "low_confidence_pages",
        "error_message",
        "progress_stage",
        "progress_pct",
        "page_count",
        "approval_id",
        "supersedes_document_id",
        "source",
        "effective_date",
        "lifecycle_status",
        "version",
        "visibility",
        "doc_type",
    ):
        op.drop_column("policy_documents", col_name)

    # 3. Drop pg_trgm extension
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
