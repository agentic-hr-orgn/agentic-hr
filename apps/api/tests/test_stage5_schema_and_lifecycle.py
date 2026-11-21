"""
Comprehensive Schema, HNSW/FTS/Trigram Index, Forced RLS & Dual-Lifecycle Test Suite
for Stage 5 Prompt 1.

Verifies:
1. Dual-lifecycle state machine (`assert_ingestion_transition`, `assert_lifecycle_transition`,
   `compute_display_status`) proving indexing alone can NEVER make a policy `active`,
   `needs_review -> indexed | queued` works cleanly, and `superseded`/`archived` are terminal.
2. `PGVector(768)` validation, formatting/parsing, and `.cosine_distance()` SQL compilation.
3. PostgreSQL extensions (`pg_trgm`, `vector`) and Stage 5 indexes (HNSW `vector_cosine_ops`,
   GIN `tsv`, GIN `gin_trgm_ops`, `(tenant_id, lower(title), version)` unique index, reuse lookup).
4. Forced RLS (`relrowsecurity=true`, `relforcerowsecurity=true`), `agentic_hr_migrator` ownership,
   revoked `TRUNCATE`, bogus tenant `00000000-0000-0000-0000-000000000000` returning 0 rows, and
   6-point cross-tenant isolation across `policy_documents`, `policy_document_pages`,
   `policy_chunks`, and `policy_chunk_embeddings` (Acme vs Globex) with clean teardown.
5. Full SQLAlchemy ORM round-trip (`PolicyDocument -> PolicyDocumentPage -> PolicyChunk ->
   PolicyChunkEmbedding`) including 768-dim L2-normalized vector search, version uniqueness,
   and `ON DELETE CASCADE`.
"""

import math
import uuid

import asyncpg
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.db.session import AsyncSessionLocal
from app.db.types import PGVector, format_pgvector, parse_pgvector
from app.models import (
    PolicyChunk,
    PolicyChunkEmbedding,
    PolicyDocument,
    PolicyDocumentPage,
)
from app.models.tenant import set_tenant_context
from app.services.policy_lifecycle import (
    PolicyTransitionError,
    assert_ingestion_transition,
    assert_lifecycle_transition,
    compute_display_status,
)
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    MultiTenantTestContext,
    assert_table_tenant_isolation,
    get_app_connection,
)

STAGE5_POLICY_TABLES: tuple[str, ...] = (
    "policy_documents",
    "policy_document_pages",
    "policy_chunks",
    "policy_chunk_embeddings",
)


def _normalized_vector(seed_index: int = 0, dim: int = 768) -> list[float]:
    """Builds a deterministic unit-length (L2-normalized) vector of length `dim`."""
    raw = [0.0] * dim
    raw[seed_index % dim] = 3.0
    raw[(seed_index + 1) % dim] = 4.0
    norm = math.sqrt(sum(v * v for v in raw))
    return [v / norm for v in raw]


# ==============================================================================
# 1. Dual-Lifecycle State Machine Unit Tests
# ==============================================================================
def test_01_ingestion_status_transitions() -> None:
    """Verifies valid and invalid transitions on `ingestion_status`."""
    # Valid transitions
    assert assert_ingestion_transition("queued_stub", "queued") == "queued"
    assert assert_ingestion_transition("queued", "processing") == "processing"
    assert assert_ingestion_transition("processing", "indexed") == "indexed"
    assert assert_ingestion_transition("processing", "needs_review") == "needs_review"
    assert assert_ingestion_transition("processing", "failed") == "failed"
    assert assert_ingestion_transition("failed", "queued") == "queued"
    assert assert_ingestion_transition("needs_review", "queued") == "queued"
    assert assert_ingestion_transition("needs_review", "indexed") == "indexed"
    assert assert_ingestion_transition("indexed", "queued") == "queued"

    # Invalid jumps & self-transitions
    for curr, target in (
        ("queued", "indexed"),
        ("queued", "failed"),
        ("queued_stub", "indexed"),
        ("queued_stub", "processing"),
        ("processing", "queued"),
        ("failed", "indexed"),
        ("indexed", "processing"),
        ("queued", "queued"),
        ("indexed", "indexed"),
    ):
        with pytest.raises(PolicyTransitionError):
            assert_ingestion_transition(curr, target)

    with pytest.raises(PolicyTransitionError) as exc_info:
        assert_ingestion_transition("bogus_state", "queued")
    assert exc_info.value.code == "INVALID_INGESTION_STATUS"


def test_02_lifecycle_transitions_and_approval_gate() -> None:
    """
    Proves indexing alone can NEVER transition a policy to `active`,
    non-indexed documents cannot enter `review`, `review -> active` requires an approved
    `policy.publish` gate, and `superseded` / `archived` are strictly terminal.
    """
    sample_approval = uuid.uuid4()

    # 1. Indexing alone (draft + indexed) can NEVER skip directly to active
    with pytest.raises(PolicyTransitionError) as direct_err:
        assert_lifecycle_transition(
            "draft",
            "active",
            ingestion_status="indexed",
            approval_id=sample_approval,
            approval_status="approved",
        )
    assert direct_err.value.code == "APPROVAL_WORKFLOW_REQUIRED"

    # 2. draft -> review is blocked for every non-indexed ingestion status
    for non_indexed in ("queued_stub", "queued", "processing", "needs_review", "failed"):
        with pytest.raises(PolicyTransitionError) as not_idx_err:
            assert_lifecycle_transition(
                "draft",
                "review",
                ingestion_status=non_indexed,
            )
        assert not_idx_err.value.code == "DOCUMENT_NOT_INDEXED"

    # 3. draft -> review succeeds ONLY when ingestion_status == 'indexed'
    assert (
        assert_lifecycle_transition(
            "draft",
            "review",
            ingestion_status="indexed",
        )
        == "review"
    )

    # 4. review -> active fails without approved policy.publish approval
    for bad_id, bad_status in (
        (None, "approved"),
        (sample_approval, None),
        (sample_approval, "pending"),
        (sample_approval, "rejected"),
        (sample_approval, "changes_requested"),
    ):
        with pytest.raises(PolicyTransitionError) as appr_err:
            assert_lifecycle_transition(
                "review",
                "active",
                ingestion_status="indexed",
                approval_id=bad_id,
                approval_status=bad_status,
            )
        assert appr_err.value.code == "APPROVAL_REQUIRED"

    # review -> active also fails if document was re-queued and is not currently indexed
    with pytest.raises(PolicyTransitionError) as reindex_err:
        assert_lifecycle_transition(
            "review",
            "active",
            ingestion_status="processing",
            approval_id=sample_approval,
            approval_status="approved",
        )
    assert reindex_err.value.code == "DOCUMENT_NOT_INDEXED"

    # 5. Valid approval promotes review -> active
    assert (
        assert_lifecycle_transition(
            "review",
            "active",
            ingestion_status="indexed",
            approval_id=sample_approval,
            approval_status="approved",
        )
        == "active"
    )

    # 6. Rejection/changes requested moves review -> draft; active -> superseded/archived
    assert assert_lifecycle_transition("review", "draft", ingestion_status="indexed") == "draft"
    assert (
        assert_lifecycle_transition("active", "superseded", ingestion_status="indexed")
        == "superseded"
    )
    assert (
        assert_lifecycle_transition("active", "archived", ingestion_status="indexed") == "archived"
    )
    assert assert_lifecycle_transition("draft", "archived", ingestion_status="failed") == "archived"
    assert (
        assert_lifecycle_transition("review", "archived", ingestion_status="indexed") == "archived"
    )

    # 7. Terminal states (superseded / archived) cannot transition anywhere
    for terminal in ("superseded", "archived"):
        for target in ("draft", "review", "active", "superseded", "archived"):
            with pytest.raises(PolicyTransitionError) as term_err:
                assert_lifecycle_transition(
                    terminal,
                    target,
                    ingestion_status="indexed",
                    approval_id=sample_approval,
                    approval_status="approved",
                )
            assert term_err.value.code == "TERMINAL_LIFECYCLE_STATUS"


def test_03_compute_display_status_matrix() -> None:
    """Verifies unified display status badge mapping from the two DB columns."""
    assert compute_display_status("queued_stub", "draft") == "DRAFT"
    assert compute_display_status("queued", "draft") == "PROCESSING"
    assert compute_display_status("processing", "draft") == "PROCESSING"
    assert compute_display_status("needs_review", "draft") == "REVIEW"
    assert compute_display_status("failed", "draft") == "FAILED"
    assert compute_display_status("indexed", "draft") == "DRAFT"
    assert compute_display_status("indexed", "review") == "REVIEW"
    assert compute_display_status("indexed", "active") == "ACTIVE"
    assert compute_display_status("indexed", "superseded") == "SUPERSEDED"
    assert compute_display_status("indexed", "archived") == "ARCHIVED"
    assert compute_display_status("failed", "archived") == "ARCHIVED"


def test_04_pgvector_type_validation_and_cosine_distance_compiler() -> None:
    """Verifies PGVector(768) serialization, parsing, safety checks, and `<=>` SQL compilation."""
    vec = _normalized_vector(seed_index=3)
    formatted = format_pgvector(vec, expected_dim=768)
    assert formatted.startswith("[") and formatted.endswith("]")

    parsed = parse_pgvector(formatted, expected_dim=768)
    assert parsed is not None
    assert len(parsed) == 768
    assert math.isclose(parsed[3], 0.6, rel_tol=1e-6)
    assert math.isclose(parsed[4], 0.8, rel_tol=1e-6)

    # Wrong dimension rejected
    with pytest.raises(ValueError, match="Expected 768-dimensional vector"):
        format_pgvector([0.1, 0.2], expected_dim=768)

    # Non-finite values (NaN / Inf) rejected fail-closed
    bad_nan = [0.0] * 768
    bad_nan[0] = float("nan")
    with pytest.raises(ValueError, match="not finite"):
        format_pgvector(bad_nan, expected_dim=768)

    bad_inf = [0.0] * 768
    bad_inf[10] = float("inf")
    with pytest.raises(ValueError, match="not finite"):
        format_pgvector(bad_inf, expected_dim=768)

    # Verify cosine_distance comparator compiles to `<=>`
    pg_vec = PGVector(768)
    assert pg_vec.get_col_spec() == "vector(768)"
    expr = PolicyChunkEmbedding.embedding.cosine_distance(vec)
    compiled = str(expr.compile(dialect=postgresql.dialect()))
    assert "<=>" in compiled


# ==============================================================================
# 2. PostgreSQL Extensions, HNSW / GIN FTS / Trigram Indexes & RLS Metadata
# ==============================================================================
@pytest.mark.asyncio
async def test_05_extensions_hnsw_fts_trgm_indexes_and_rls_metadata() -> None:
    """
    Verifies live PostgreSQL catalog metadata:
    - Extensions `vector` and `pg_trgm` installed.
    - `pg_indexes` contains HNSW cosine (`m=16`, `ef_construction=64`), GIN `tsv`,
      GIN `gin_trgm_ops`, `(tenant_id, lower(title), version)` unique index, and reuse lookup.
    - All 4 policy tables have `relrowsecurity=true`, `relforcerowsecurity=true`,
      owner `agentic_hr_migrator`, and `TRUNCATE` revoked from `agentic_hr_app`.
    """
    conn = await get_app_connection()
    try:
        exts = await conn.fetch(
            "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm');"
        )
        assert {r["extname"] for r in exts} == {"vector", "pg_trgm"}

        idx_rows = await conn.fetch(
            """
            SELECT indexname, tablename, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = ANY($1::text[]);
            """,
            list(STAGE5_POLICY_TABLES),
        )
        idx_map = {r["indexname"]: r["indexdef"] for r in idx_rows}

        hnsw_def = idx_map.get("ix_policy_chunk_embeddings_hnsw_cosine", "")
        assert "USING hnsw" in hnsw_def
        assert "vector_cosine_ops" in hnsw_def
        assert "m='16'" in hnsw_def or "m=16" in hnsw_def
        assert "ef_construction='64'" in hnsw_def or "ef_construction=64" in hnsw_def

        tsv_def = idx_map.get("ix_policy_chunks_tsv_gin", "")
        assert "USING gin (tsv)" in tsv_def

        trgm_def = idx_map.get("ix_policy_chunks_normalized_text_trgm", "")
        assert "USING gin" in trgm_def
        assert "gin_trgm_ops" in trgm_def

        uq_title_ver = idx_map.get("uq_policy_documents_tenant_lower_title_version", "")
        assert "UNIQUE INDEX" in uq_title_ver
        assert "lower((title)::text)" in uq_title_ver
        assert "version" in uq_title_ver

        reuse_def = idx_map.get("ix_policy_chunk_embeddings_reuse_lookup", "")
        assert "tenant_id, model, dim, content_hash" in reuse_def

        # Verify Forced RLS, owner, and TRUNCATE revocation on all 4 tables
        for tbl in STAGE5_POLICY_TABLES:
            meta = await conn.fetchrow(
                """
                SELECT
                    c.relrowsecurity,
                    c.relforcerowsecurity,
                    r.rolname AS owner_name
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
                WHERE n.nspname = 'public' AND c.relname = $1;
                """,
                tbl,
            )
            assert meta is not None, f"Missing table public.{tbl}"
            assert meta["relrowsecurity"] is True
            assert meta["relforcerowsecurity"] is True
            assert meta["owner_name"] == "agentic_hr_migrator"

            can_truncate = await conn.fetchval(
                "SELECT has_table_privilege('agentic_hr_app', $1, 'TRUNCATE');",
                f"public.{tbl}",
            )
            assert can_truncate is False, f"TRUNCATE must be revoked on public.{tbl}"
    finally:
        await conn.close()


# ==============================================================================
# 3. Live Cross-Tenant Forced RLS Isolation (Acme vs Globex + Bogus UUID)
# ==============================================================================
@pytest.mark.asyncio
async def test_06_stage5_policy_tables_rls_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies live PostgreSQL `agentic_hr_app` isolation across `policy_documents`,
    `policy_document_pages`, `policy_chunks`, and `policy_chunk_embeddings`:
    - Bogus tenant UUID `00000000-0000-0000-0000-000000000000` returns 0 rows.
    - Full 6-point `assert_table_tenant_isolation` (Acme vs Globex) passes on all 4 tables.
    - Clean teardown removes all temporary rows created during the test.
    """
    tenant_a = isolation_context.company_a.tenant_id
    tenant_b = isolation_context.company_b.tenant_id
    bogus_tenant = "00000000-0000-0000-0000-000000000000"
    suffix = uuid.uuid4().hex[:8]

    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    created_doc_ids: list[str] = []
    created_ids: dict[str, tuple[str, str]] = {}

    try:
        vec_a = format_pgvector(_normalized_vector(0), expected_dim=768)
        vec_b = format_pgvector(_normalized_vector(1), expected_dim=768)

        pair_ids: dict[str, list[str]] = {tbl: [] for tbl in STAGE5_POLICY_TABLES}
        for label, t_id, vec_str in (("a", tenant_a, vec_a), ("b", tenant_b, vec_b)):
            doc_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.policy_documents (
                        tenant_id, title, file_name, mime_type, size_bytes,
                        storage_provider, storage_key, ingestion_status, lifecycle_status
                    )
                    VALUES (
                        $1::uuid, $2, 'leave.pdf', 'application/pdf', 1024,
                        'local', $3, 'indexed', 'draft'
                    )
                    RETURNING id;
                    """,
                    t_id,
                    f"Stage5 RLS Policy {label} {suffix}",
                    f"rls/{label}/{suffix}/leave.pdf",
                )
            )
            created_doc_ids.append(doc_id)
            pair_ids["policy_documents"].append(doc_id)

            page_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.policy_document_pages (
                        tenant_id, document_id, page_number, raw_text, cleaned_text,
                        language, quality_score, extraction_status
                    )
                    VALUES (
                        $1::uuid, $2::uuid, 1, 'Annual leave policy', 'annual leave policy',
                        'en', 0.99, 'ok'
                    )
                    RETURNING id;
                    """,
                    t_id,
                    doc_id,
                )
            )
            pair_ids["policy_document_pages"].append(page_id)

            chunk_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.policy_chunks (
                        tenant_id, document_id, version, chunk_index, section_path,
                        page_start, page_end, page_range, text, normalized_text,
                        content_hash, token_count, language, visibility, tsv
                    )
                    VALUES (
                        $1::uuid, $2::uuid, 1, 0, 'Leave > Annual',
                        1, 1, '1', 'Annual leave is 20 days.', 'annual leave is 20 days',
                        $3, 6, 'en', 'all', to_tsvector('simple', 'annual leave is 20 days')
                    )
                    RETURNING id;
                    """,
                    t_id,
                    doc_id,
                    f"hash-{label}-{suffix}",
                )
            )
            pair_ids["policy_chunks"].append(chunk_id)

            emb_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.policy_chunk_embeddings (
                        tenant_id, chunk_id, content_hash, model, dim, embedding
                    )
                    VALUES (
                        $1::uuid, $2::uuid, $3, 'gemini-embedding-001', 768, $4::vector
                    )
                    RETURNING id;
                    """,
                    t_id,
                    chunk_id,
                    f"hash-{label}-{suffix}",
                    vec_str,
                )
            )
            pair_ids["policy_chunk_embeddings"].append(emb_id)

        for tbl, ids in pair_ids.items():
            created_ids[tbl] = (ids[0], ids[1])

        # 1. Bogus tenant UUID returns 0 rows across all 4 tables under agentic_hr_app
        app_conn = await get_app_connection()
        try:
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    bogus_tenant,
                )
                for tbl in STAGE5_POLICY_TABLES:
                    cnt = await app_conn.fetchval(f"SELECT count(*) FROM public.{tbl};")
                    assert cnt == 0, f"CRITICAL RLS LEAK: public.{tbl} returned {cnt} rows!"
        finally:
            await app_conn.close()

        # 2. Full 6-point isolation across all 4 tables
        await assert_table_tenant_isolation(
            table_name="policy_documents",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["policy_documents"][0],
            company_b_row_id=created_ids["policy_documents"][1],
            insert_sql_factory=lambda target_tid: (
                (
                    "INSERT INTO public.policy_documents "
                    "(tenant_id, title, file_name, mime_type, storage_key) "
                    "VALUES ($1::uuid, $2, 'cross.pdf', 'application/pdf', 'k');"
                ),
                [target_tid, f"Cross Policy {uuid.uuid4().hex[:6]}"],
            ),
        )
        await assert_table_tenant_isolation(
            table_name="policy_document_pages",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["policy_document_pages"][0],
            company_b_row_id=created_ids["policy_document_pages"][1],
            insert_sql_factory=lambda target_tid: (
                (
                    "INSERT INTO public.policy_document_pages "
                    "(tenant_id, document_id, page_number, raw_text, cleaned_text) "
                    "VALUES ($1::uuid, $2::uuid, 99, 'x', 'x');"
                ),
                [target_tid, created_ids["policy_documents"][1]],
            ),
        )
        await assert_table_tenant_isolation(
            table_name="policy_chunks",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["policy_chunks"][0],
            company_b_row_id=created_ids["policy_chunks"][1],
            insert_sql_factory=lambda target_tid: (
                (
                    "INSERT INTO public.policy_chunks "
                    "(tenant_id, document_id, chunk_index, text, normalized_text, content_hash) "
                    "VALUES ($1::uuid, $2::uuid, 99, 'x', 'x', 'h');"
                ),
                [target_tid, created_ids["policy_documents"][1]],
            ),
        )
        await assert_table_tenant_isolation(
            table_name="policy_chunk_embeddings",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["policy_chunk_embeddings"][0],
            company_b_row_id=created_ids["policy_chunk_embeddings"][1],
            insert_sql_factory=lambda target_tid: (
                (
                    "INSERT INTO public.policy_chunk_embeddings "
                    "(tenant_id, chunk_id, content_hash, model, dim, embedding) "
                    "VALUES ($1::uuid, $2::uuid, 'h', 'gemini-embedding-001', 768, $3::vector);"
                ),
                [target_tid, created_ids["policy_chunks"][1], vec_b],
            ),
        )
    finally:
        if created_doc_ids:
            await admin_conn.execute(
                "DELETE FROM public.policy_documents WHERE id = ANY($1::uuid[]);",
                created_doc_ids,
            )
        await admin_conn.close()


# ==============================================================================
# 4. SQLAlchemy ORM Round-Trip, Cosine Query, Version Uniqueness & CASCADE
# ==============================================================================
@pytest.mark.asyncio
async def test_07_orm_roundtrip_cosine_query_versioning_and_cascade(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies SQLAlchemy 2.0 ORM persistence and RLS-scoped query across all 4 Stage 5 models,
    including `.cosine_distance()` HNSW query, `(tenant_id, lower(title), version)` versioning,
    and `ON DELETE CASCADE` cleanup.
    """
    acme_tenant_id = uuid.UUID(isolation_context.company_a.tenant_id)
    suffix = uuid.uuid4().hex[:8]
    doc_title = f"ORM Bilingual Leave Policy {suffix}"
    vec_close = _normalized_vector(0)
    vec_far = _normalized_vector(10)

    created_doc_ids: list[uuid.UUID] = []

    try:
        # 1. Insert PolicyDocument (v1) -> Page -> 2 Chunks -> 2 Embeddings via AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, acme_tenant_id)

            doc_v1 = PolicyDocument(
                tenant_id=acme_tenant_id,
                title=doc_title,
                doc_type="company_policy",
                visibility="all",
                version=1,
                file_name=f"leave_{suffix}.pdf",
                mime_type="application/pdf",
                size_bytes=4096,
                storage_provider="local",
                storage_key=f"{acme_tenant_id}/policies/{suffix}/leave.pdf",
                ingestion_status="indexed",
                lifecycle_status="draft",
                page_count=1,
                progress_pct=100,
                progress_stage="completed",
            )
            session.add(doc_v1)
            await session.flush()
            created_doc_ids.append(doc_v1.id)

            page = PolicyDocumentPage(
                tenant_id=acme_tenant_id,
                document_id=doc_v1.id,
                page_number=1,
                raw_text="Annual leave is 20 days. سالانہ چھٹی 20 دن۔",
                cleaned_text="annual leave is 20 days سالانہ چھٹی 20 دن",
                language="mixed",
                quality_score=0.98,
                extraction_status="ok",
            )
            session.add(page)

            chunk_0 = PolicyChunk(
                tenant_id=acme_tenant_id,
                document_id=doc_v1.id,
                version=1,
                chunk_index=0,
                section_path="Leave Policy > Annual Leave",
                page_start=1,
                page_end=1,
                page_range="1",
                text="Annual leave is 20 days.",
                normalized_text="annual leave is 20 days",
                content_hash=f"hash0-{suffix}",
                token_count=6,
                language="en",
                visibility="all",
                tsv=func.to_tsvector("simple", "annual leave is 20 days"),
            )
            chunk_1 = PolicyChunk(
                tenant_id=acme_tenant_id,
                document_id=doc_v1.id,
                version=1,
                chunk_index=1,
                section_path="Leave Policy > Urdu Summary",
                page_start=1,
                page_end=1,
                page_range="1",
                text="سالانہ چھٹی 20 دن۔",
                normalized_text="سالانہ چھٹی 20 دن",
                content_hash=f"hash1-{suffix}",
                token_count=5,
                language="ur",
                visibility="all",
                tsv=func.to_tsvector("simple", "سالانہ چھٹی 20 دن"),
            )
            session.add_all([chunk_0, chunk_1])
            await session.flush()

            emb_0 = PolicyChunkEmbedding(
                tenant_id=acme_tenant_id,
                chunk_id=chunk_0.id,
                content_hash=chunk_0.content_hash,
                model="gemini-embedding-001",
                dim=768,
                embedding=vec_close,
            )
            emb_1 = PolicyChunkEmbedding(
                tenant_id=acme_tenant_id,
                chunk_id=chunk_1.id,
                content_hash=chunk_1.content_hash,
                model="gemini-embedding-001",
                dim=768,
                embedding=vec_far,
            )
            session.add_all([emb_0, emb_1])
            await session.commit()

            doc_v1_id = doc_v1.id
            chunk_0_id = chunk_0.id

        # 2. Query back via cosine_distance helper & verify ranking order
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, acme_tenant_id)
            dist_col = PolicyChunkEmbedding.embedding.cosine_distance(vec_close).label("dist")
            rows = (
                await session.execute(
                    select(PolicyChunkEmbedding, dist_col)
                    .join(PolicyChunk, PolicyChunkEmbedding.chunk_id == PolicyChunk.id)
                    .where(PolicyChunk.document_id == doc_v1_id)
                    .order_by(dist_col.asc())
                )
            ).all()
            assert len(rows) == 2
            top_emb, top_dist = rows[0]
            assert top_emb.chunk_id == chunk_0_id
            assert top_dist < 1e-5
            assert len(top_emb.embedding) == 768

        # 3. Version 2 with same title (case-insensitive) is allowed; duplicate v1 is rejected
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, acme_tenant_id)
            doc_v2 = PolicyDocument(
                tenant_id=acme_tenant_id,
                title=doc_title.lower(),
                version=2,
                file_name=f"leave_v2_{suffix}.pdf",
                mime_type="application/pdf",
                size_bytes=5120,
                storage_provider="local",
                storage_key=f"{acme_tenant_id}/policies/{suffix}/leave_v2.pdf",
                supersedes_document_id=doc_v1_id,
            )
            session.add(doc_v2)
            await session.commit()
            created_doc_ids.append(doc_v2.id)

        with pytest.raises(IntegrityError):
            async with AsyncSessionLocal() as session:
                await set_tenant_context(session, acme_tenant_id)
                dup_v1 = PolicyDocument(
                    tenant_id=acme_tenant_id,
                    title=doc_title.upper(),
                    version=1,
                    file_name=f"leave_dup_{suffix}.pdf",
                    mime_type="application/pdf",
                    size_bytes=1024,
                    storage_provider="local",
                    storage_key=f"{acme_tenant_id}/policies/{suffix}/leave_dup.pdf",
                )
                session.add(dup_v1)
                await session.commit()

        # 4. Deleting PolicyDocument cascades to pages, chunks, and embeddings
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, acme_tenant_id)
            await session.execute(delete(PolicyDocument).where(PolicyDocument.id == doc_v1_id))
            await session.commit()

            rem_pages = await session.scalar(
                select(func.count(PolicyDocumentPage.id)).where(
                    PolicyDocumentPage.document_id == doc_v1_id
                )
            )
            rem_chunks = await session.scalar(
                select(func.count(PolicyChunk.id)).where(PolicyChunk.document_id == doc_v1_id)
            )
            rem_embs = await session.scalar(
                select(func.count(PolicyChunkEmbedding.id)).where(
                    PolicyChunkEmbedding.chunk_id == chunk_0_id
                )
            )
            assert rem_pages == 0
            assert rem_chunks == 0
            assert rem_embs == 0
    finally:
        if created_doc_ids:
            admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
            try:
                await admin_conn.execute(
                    "DELETE FROM public.policy_documents WHERE id = ANY($1::uuid[]);",
                    [str(d) for d in created_doc_ids],
                )
            finally:
                await admin_conn.close()
