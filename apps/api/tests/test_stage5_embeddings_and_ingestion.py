"""
Stage 5 Prompt 3 Verification Suite:
- Multilingual Embeddings (`gemini-embedding-001` 768D + mandatory L2 normalization) via `ModelGateway`
- Deterministic Bilingual (Urdu <-> English) Concept + N-Gram Fake Embedder
- Idempotent 4-Step `PolicyIngestionService` (`INGEST_INLINE=true` + Hash Cache Reuse)
- Sandboxed Temporal `PolicyIngestionWorkflow` (`sandboxed=True`)
"""

import math
import uuid

import asyncpg
import pytest
from sqlalchemy import select
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.core.config import settings
from app.core.fake_model import fake_model_provider, l2_normalize
from app.core.model_gateway import (
    ModelGateway,
    ModelRoute,
    ModelRouteUnavailableError,
)
from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.policy_chunk import PolicyChunk
from app.models.policy_chunk_embedding import PolicyChunkEmbedding
from app.models.policy_document import PolicyDocument
from app.models.policy_document_page import PolicyDocumentPage
from app.models.tenant import set_tenant_context
from app.services.policy_ingestion_service import (
    PolicyIngestionService,
    run_policy_ingestion_inline,
)
from app.services.storage_helper import StorageHelper
from app.workflows import (
    PolicyIngestionWorkflow,
    chunk_policy_document_activity,
    embed_policy_chunks_activity,
    finalize_policy_ingestion_activity,
    mark_policy_ingestion_failed_activity,
    parse_policy_document_activity,
)
from tests.isolation_harness import MIGRATION_DB_URL, MultiTenantTestContext


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes cosine similarity (dot product of unit-normalized vectors)."""
    return sum(a * b for a, b in zip(vec_a, vec_b, strict=True))


@pytest.fixture(autouse=True)
def _reset_fake_embedder():
    fake_model_provider.reset()
    yield
    fake_model_provider.reset()


# ==============================================================================
# 1. L2 Normalization Unit Norm & Zero-Vector Safety
# ==============================================================================
def test_01_l2_normalize_unit_norm_and_zero_vector_safety() -> None:
    """
    Verifies `l2_normalize` produces unit norm (||v||_2 == 1.0), handles all-zero vectors
    safely without ZeroDivisionError/NaN, and rejects empty or non-finite vectors.
    """
    raw = [3.0, 4.0] + [0.0] * 766
    normed = l2_normalize(raw)
    assert len(normed) == 768
    norm_val = math.sqrt(sum(v * v for v in normed))
    assert math.isclose(norm_val, 1.0, rel_tol=1e-5)
    assert math.isclose(normed[0], 0.6, rel_tol=1e-5)
    assert math.isclose(normed[1], 0.8, rel_tol=1e-5)

    # Zero-vector protection returns all zeros safely without ZeroDivisionError
    zeros = l2_normalize([0.0] * 768)
    assert len(zeros) == 768
    assert all(v == 0.0 for v in zeros)

    # Empty or non-finite vectors rejected
    with pytest.raises(ValueError, match="empty vector"):
        l2_normalize([])

    with pytest.raises(ValueError, match="not finite"):
        l2_normalize([float("nan")] + [0.0] * 767)

    with pytest.raises(ValueError, match="not finite"):
        l2_normalize([float("inf")] + [0.0] * 767)


# ==============================================================================
# 2. ModelGateway.embed_texts, Batching, Task Types, Private Stub & Bilingual Ranking
# ==============================================================================
@pytest.mark.asyncio
async def test_02_model_gateway_embed_texts_and_bilingual_fake_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifies:
    - `ModelGateway.embed_texts` respects `RETRIEVAL_DOCUMENT` vs `RETRIEVAL_QUERY`, batches
      requests at `EMBEDDING_BATCH_SIZE` (32), L2-normalizes all 768-dim vectors, and records
      usage-only telemetry.
    - Reserved `'private'` route (`bge-m3`) fails closed when `PRIVATE_MODEL_ENDPOINT` is missing.
    - Deterministic bilingual fake embedder ranks an Urdu leave/remote-work query closer to its
      matching English policy chunk than an unrelated IT Password chunk (and vice versa).
    """
    gw = ModelGateway()
    gw.trace_sink.clear()
    tenant_id = str(uuid.uuid4())

    # 1. Batching verification (35 texts with batch size 32 -> 2 batches: 32 + 3)
    sample_texts = [f"Annual leave policy clause {i} for full-time staff." for i in range(35)]
    batch_res = await gw.embed_texts(
        sample_texts,
        task_type="RETRIEVAL_DOCUMENT",
        tenant_id=tenant_id,
    )
    assert batch_res.model == "gemini-embedding-001"
    assert batch_res.dim == 768
    assert batch_res.task_type == "RETRIEVAL_DOCUMENT"
    assert batch_res.batch_count == 2
    assert len(batch_res.embeddings) == 35
    for vec in batch_res.embeddings:
        assert len(vec) == 768
        assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-5)

    assert len(fake_model_provider.embedding_call_history) == 2
    assert fake_model_provider.embedding_call_history[0]["batch_size"] == 32
    assert fake_model_provider.embedding_call_history[1]["batch_size"] == 3
    assert fake_model_provider.embedding_call_history[0]["task_type"] == "RETRIEVAL_DOCUMENT"

    # Usage-only telemetry redacts input text
    assert len(gw.trace_sink.traces) == 1
    trace = gw.trace_sink.traces[0]
    assert trace["name"] == "model_gateway_embed"
    assert trace["metadata"]["batch_count"] == 2
    assert trace["metadata"]["text_count"] == 35
    assert "Annual leave" not in str(trace)

    # 2. Reserved "private" (bge-m3) route fails closed when unconfigured
    monkeypatch.setattr(settings, "PRIVATE_MODEL_ENDPOINT", None)
    with pytest.raises(ModelRouteUnavailableError, match="bge-m3"):
        await gw.embed_texts(
            ["Confidential board policy"],
            task_type="RETRIEVAL_DOCUMENT",
            tenant_id=tenant_id,
            route=ModelRoute.PRIVATE,
        )

    # 3. Cross-language Urdu <-> English semantic ranking
    en_leave_chunk = (
        "Annual Leave Policy: Full-time employees are entitled to 20 days of paid annual leave "
        "and vacation time off per calendar year."
    )
    en_remote_chunk = (
        "Remote Work Policy: Employees may work from home (WFH) up to two days per week "
        "under the hybrid telecommute schedule."
    )
    en_it_chunk = (
        "IT Security Policy: Corporate laptop passwords must use two-factor authentication (MFA) "
        "and VPN encryption."
    )
    ur_leave_chunk = "سالانہ چھٹی کی پالیسی: تمام مستقل ملازمین کو ہر سال بیس دن کی سالانہ چھٹی اور رخصت ملتی ہے۔"
    ur_it_chunk = "آئی ٹی سیکیورٹی پالیسی: تمام ملازمین کے لیے لیپ ٹاپ پاس ورڈ اور سیکیورٹی کی تصدیق لازمی ہے۔"

    doc_vecs = (
        await gw.embed_texts(
            [en_leave_chunk, en_remote_chunk, en_it_chunk, ur_leave_chunk, ur_it_chunk],
            task_type="RETRIEVAL_DOCUMENT",
            tenant_id=tenant_id,
        )
    ).embeddings
    vec_en_leave, vec_en_remote, vec_en_it, vec_ur_leave, vec_ur_it = doc_vecs

    ur_leave_query = "سالانہ چھٹی کتنے دن ملتی ہے؟"
    ur_remote_query = "گھر سے کام اور ریموٹ ورک کی پالیسی کیا ہے؟"
    en_leave_query = "How many annual paid leave days do employees get each year?"

    q_vecs = (
        await gw.embed_texts(
            [ur_leave_query, ur_remote_query, en_leave_query],
            task_type="RETRIEVAL_QUERY",
            tenant_id=tenant_id,
        )
    ).embeddings
    q_ur_leave, q_ur_remote, q_en_leave = q_vecs

    # Urdu leave query -> closer to English Leave chunk than English IT chunk
    sim_ur_q_to_en_leave = _cosine_similarity(q_ur_leave, vec_en_leave)
    sim_ur_q_to_en_it = _cosine_similarity(q_ur_leave, vec_en_it)
    assert sim_ur_q_to_en_leave > sim_ur_q_to_en_it + 0.25

    # Urdu remote-work query -> closer to English Remote Work chunk than English IT chunk
    sim_ur_rem_to_en_remote = _cosine_similarity(q_ur_remote, vec_en_remote)
    sim_ur_rem_to_en_it = _cosine_similarity(q_ur_remote, vec_en_it)
    assert sim_ur_rem_to_en_remote > sim_ur_rem_to_en_it + 0.25

    # English leave query -> closer to Urdu Leave chunk than Urdu IT chunk
    sim_en_q_to_ur_leave = _cosine_similarity(q_en_leave, vec_ur_leave)
    sim_en_q_to_ur_it = _cosine_similarity(q_en_leave, vec_ur_it)
    assert sim_en_q_to_ur_leave > sim_en_q_to_ur_it + 0.25


# ==============================================================================
# 3. Inline Ingestion & Idempotent Content-Hash Embedding Reuse (Same Doc + v2)
# ==============================================================================
@pytest.mark.asyncio
async def test_03_inline_ingestion_and_idempotent_hash_reuse_same_doc_and_v2(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - `run_policy_ingestion_inline` runs Steps 1-4 (`queued -> processing -> indexed`) while
      preserving `lifecycle_status == 'draft'`.
    - Step 1 (`parse_and_store_pages`) handles an already-`'processing'` document idempotently.
    - Re-running ingestion on the same document makes 0 calls to `ModelGateway.embed_texts`.
    - Uploading `version=2` with 1 edited section and 2 unchanged sections reuses cached
      embeddings from `policy_chunk_embeddings` and calls `embed_texts` ONLY for the 1 edited chunk.
    """
    tenant_id = uuid.UUID(isolation_context.company_b.tenant_id)
    suffix = uuid.uuid4().hex[:8]
    created_doc_ids: list[str] = []
    stored_keys: list[str] = []

    sec1 = (
        "# 1. Annual Leave Entitlement\n"
        "1.1 Confirmed full-time employees at Globex Corporation receive 20 working days of "
        "paid annual leave per calendar year accrued monthly."
    )
    sec2 = (
        "# 2. Remote Work Eligibility\n"
        "2.1 Engineering and product staff may work from home up to two days per week with "
        "prior line-manager approval and active VPN connectivity."
    )
    sec3_v1 = (
        "# 3. Sick Leave Notification\n"
        "3.1 Employees must notify their supervisor before 10:00 PKT and provide a medical "
        "certificate for absences exceeding two consecutive days."
    )
    sec3_v2 = (
        "# 3. Sick Leave Notification\n"
        "3.1 UPDATED 2026: Employees receive 12 paid medical leave days and must submit a "
        "registered clinic certificate only when absence exceeds three consecutive days."
    )

    v1_bytes = f"{sec1}\n\n{sec2}\n\n{sec3_v1}".encode()
    v2_bytes = f"{sec1}\n\n{sec2}\n\n{sec3_v2}".encode()

    try:
        stored_v1 = await StorageHelper.store_file(
            tenant_id=tenant_id,
            category="policies",
            file_name=f"handbook_v1_{suffix}.txt",
            content=v1_bytes,
            mime_type="text/plain",
            storage_provider="local",
        )
        stored_keys.append(stored_v1.storage_key)

        stored_v2 = await StorageHelper.store_file(
            tenant_id=tenant_id,
            category="policies",
            file_name=f"handbook_v2_{suffix}.txt",
            content=v2_bytes,
            mime_type="text/plain",
            storage_provider="local",
        )
        stored_keys.append(stored_v2.storage_key)

        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            doc_v1 = PolicyDocument(
                tenant_id=tenant_id,
                title=f"Globex Employee Handbook {suffix}",
                doc_type="company_policy",
                visibility="all",
                version=1,
                file_name=f"handbook_v1_{suffix}.txt",
                mime_type="text/plain",
                size_bytes=stored_v1.size_bytes,
                storage_provider="local",
                storage_key=stored_v1.storage_key,
                ingestion_status="queued",
                lifecycle_status="draft",
            )
            session.add(doc_v1)
            await session.commit()
            doc_v1_id = doc_v1.id
            created_doc_ids.append(str(doc_v1_id))

        # 1. Initial inline ingestion on v1 embeds all 3 section chunks
        fake_model_provider.reset()
        async with AsyncSessionLocal() as session:
            summary_v1 = await run_policy_ingestion_inline(session, tenant_id, doc_v1_id)

        assert summary_v1["ingestion_status"] == "indexed"
        assert summary_v1["lifecycle_status"] == "draft"
        assert summary_v1["progress_pct"] == 100
        assert summary_v1["chunk_count"] == 3
        assert summary_v1["newly_embedded"] == 3
        assert summary_v1["already_embedded"] == 0
        assert summary_v1["reused_from_cache"] == 0
        assert len(fake_model_provider.embedding_call_history) == 1
        assert fake_model_provider.embedding_call_history[0]["batch_size"] == 3

        # 2. Re-running inline ingestion on the exact same v1 document makes 0 embed_texts calls
        fake_model_provider.reset()
        async with AsyncSessionLocal() as session:
            rerun_v1 = await run_policy_ingestion_inline(session, tenant_id, doc_v1_id)

        assert rerun_v1["ingestion_status"] == "indexed"
        assert rerun_v1["lifecycle_status"] == "draft"
        assert rerun_v1["already_embedded"] == 3
        assert rerun_v1["newly_embedded"] == 0
        assert len(fake_model_provider.embedding_call_history) == 0

        # Verify Step 1 idempotent retry when document is already in 'processing'
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            doc_row = await session.get(PolicyDocument, doc_v1_id)
            assert doc_row is not None
            doc_row.ingestion_status = "processing"
            await session.flush()
            step1_retry = await PolicyIngestionService.parse_and_store_pages(
                session,
                tenant_id=tenant_id,
                document_id=doc_v1_id,
            )
            assert step1_retry["progress_pct"] == 30
            await PolicyIngestionService.finalize_ingestion(
                session,
                tenant_id=tenant_id,
                document_id=doc_v1_id,
            )
            await session.commit()

        # 3. Upload v2 (2 unchanged sections + 1 edited section) -> embeds ONLY the 1 changed chunk
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            doc_v2 = PolicyDocument(
                tenant_id=tenant_id,
                title=f"Globex Employee Handbook {suffix}",
                doc_type="company_policy",
                visibility="all",
                version=2,
                file_name=f"handbook_v2_{suffix}.txt",
                mime_type="text/plain",
                size_bytes=stored_v2.size_bytes,
                storage_provider="local",
                storage_key=stored_v2.storage_key,
                ingestion_status="queued",
                lifecycle_status="draft",
                supersedes_document_id=doc_v1_id,
            )
            session.add(doc_v2)
            await session.commit()
            doc_v2_id = doc_v2.id
            created_doc_ids.append(str(doc_v2_id))

        fake_model_provider.reset()
        async with AsyncSessionLocal() as session:
            summary_v2 = await run_policy_ingestion_inline(session, tenant_id, doc_v2_id)

        assert summary_v2["ingestion_status"] == "indexed"
        assert summary_v2["lifecycle_status"] == "draft"
        assert summary_v2["chunk_count"] == 3
        assert summary_v2["reused_from_cache"] == 2
        assert summary_v2["newly_embedded"] == 1
        assert len(fake_model_provider.embedding_call_history) == 1
        assert fake_model_provider.embedding_call_history[0]["batch_size"] == 1
    finally:
        for key in stored_keys:
            StorageHelper.delete_local_file(tenant_id=tenant_id, storage_key=key)
        if created_doc_ids:
            admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
            try:
                await admin_conn.execute(
                    "DELETE FROM public.policy_documents WHERE id = ANY($1::uuid[]);",
                    created_doc_ids,
                )
            finally:
                await admin_conn.close()


# ==============================================================================
# 4. `needs_review` and `failed` Ingestion Status Transitions
# ==============================================================================
@pytest.mark.asyncio
async def test_04_needs_review_and_failed_ingestion_transitions(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - Low-text/scanned document transitions `queued -> processing -> needs_review` with
      `low_confidence_pages` populated while `lifecycle_status` stays `'draft'`.
    - Unreadable/missing storage file transitions `queued -> processing -> failed` with
      `error_message` and `policy_document.ingestion_failed` audit log while `lifecycle_status`
      stays `'draft'`.
    """
    tenant_id = uuid.UUID(isolation_context.company_b.tenant_id)
    suffix = uuid.uuid4().hex[:8]
    created_doc_ids: list[str] = []
    stored_keys: list[str] = []

    try:
        # 1. Low-text page (< 25 non-ws chars) triggers needs_review
        stored_low = await StorageHelper.store_file(
            tenant_id=tenant_id,
            category="policies",
            file_name=f"scanned_low_{suffix}.txt",
            content=b"scan stub",
            mime_type="text/plain",
            storage_provider="local",
        )
        stored_keys.append(stored_low.storage_key)

        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            doc_low = PolicyDocument(
                tenant_id=tenant_id,
                title=f"Scanned Policy {suffix}",
                file_name=f"scanned_low_{suffix}.txt",
                mime_type="text/plain",
                size_bytes=stored_low.size_bytes,
                storage_provider="local",
                storage_key=stored_low.storage_key,
                ingestion_status="queued",
                lifecycle_status="draft",
            )
            doc_fail = PolicyDocument(
                tenant_id=tenant_id,
                title=f"Missing File Policy {suffix}",
                file_name=f"missing_{suffix}.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                storage_provider="local",
                storage_key=f"{tenant_id}/policies/nonexistent_{suffix}.pdf",
                ingestion_status="queued",
                lifecycle_status="draft",
            )
            session.add_all([doc_low, doc_fail])
            await session.commit()
            doc_low_id = doc_low.id
            doc_fail_id = doc_fail.id
            created_doc_ids.extend([str(doc_low_id), str(doc_fail_id)])

        async with AsyncSessionLocal() as session:
            low_res = await run_policy_ingestion_inline(session, tenant_id, doc_low_id)
        assert low_res["ingestion_status"] == "needs_review"
        assert low_res["lifecycle_status"] == "draft"
        assert low_res["progress_pct"] == 100
        assert len(low_res["low_confidence_pages"]) == 1

        # 2. Missing file triggers failed status + error_message + audit log
        async with AsyncSessionLocal() as session:
            fail_res = await run_policy_ingestion_inline(session, tenant_id, doc_fail_id)
        assert fail_res["ingestion_status"] == "failed"
        assert fail_res["lifecycle_status"] == "draft"
        assert "FileNotFoundError" in (fail_res["error_message"] or "")

        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            audit_row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tenant_id,
                        AuditLog.target_id == str(doc_fail_id),
                        AuditLog.action == "policy_document.ingestion_failed",
                    )
                )
            ).scalar_one_or_none()
            assert audit_row is not None
    finally:
        for key in stored_keys:
            StorageHelper.delete_local_file(tenant_id=tenant_id, storage_key=key)
        if created_doc_ids:
            admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
            try:
                await admin_conn.execute(
                    "DELETE FROM public.policy_documents WHERE id = ANY($1::uuid[]);",
                    created_doc_ids,
                )
            finally:
                await admin_conn.close()


# ==============================================================================
# 5. Sandboxed Temporal `PolicyIngestionWorkflow` Execution
# ==============================================================================
@pytest.mark.asyncio
async def test_05_sandboxed_temporal_policy_ingestion_workflow(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies `PolicyIngestionWorkflow` executes end-to-end inside Temporal's deterministic
    workflow sandbox (`WorkflowEnvironment.start_time_skipping()`) with `sandboxed=True`,
    populating pages, chunks, and 768D L2-normalized embeddings while keeping
    `lifecycle_status == 'draft'`.
    """
    tenant_id = uuid.UUID(isolation_context.company_a.tenant_id)
    suffix = uuid.uuid4().hex[:8]
    created_doc_ids: list[str] = []
    stored_keys: list[str] = []

    bilingual_content = (
        "# 1. Remote Work & Attendance Policy\n"
        "1.1 Full-time employees may work remotely up to two days per week with manager approval.\n\n"
        "# 2. اردو حاضری اور چھٹی پالیسی\n"
        "دفعہ ۱: تمام ملازمین کو ہر سال بیس دن کی سالانہ چھٹی اور گھر سے کام کی سہولت حاصل ہے۔"
    ).encode()

    try:
        stored = await StorageHelper.store_file(
            tenant_id=tenant_id,
            category="policies",
            file_name=f"temporal_policy_{suffix}.txt",
            content=bilingual_content,
            mime_type="text/plain",
            storage_provider="local",
        )
        stored_keys.append(stored.storage_key)

        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            doc = PolicyDocument(
                tenant_id=tenant_id,
                title=f"Acme Temporal Bilingual Policy {suffix}",
                doc_type="company_policy",
                visibility="all",
                version=1,
                file_name=f"temporal_policy_{suffix}.txt",
                mime_type="text/plain",
                size_bytes=stored.size_bytes,
                storage_provider="local",
                storage_key=stored.storage_key,
                ingestion_status="queued_stub",
                lifecycle_status="draft",
            )
            session.add(doc)
            await session.commit()
            doc_id = doc.id
            created_doc_ids.append(str(doc_id))

        task_queue = f"test-stage5-ingestion-{suffix}"
        async with (
            await WorkflowEnvironment.start_time_skipping() as env,
            Worker(
                env.client,
                task_queue=task_queue,
                workflows=[PolicyIngestionWorkflow],
                activities=[
                    parse_policy_document_activity,
                    chunk_policy_document_activity,
                    embed_policy_chunks_activity,
                    finalize_policy_ingestion_activity,
                    mark_policy_ingestion_failed_activity,
                ],
            ),
        ):
            wf_result = await env.client.execute_workflow(
                PolicyIngestionWorkflow.run,
                args=[str(tenant_id), str(doc_id)],
                id=f"policy-ingest-{doc_id}",
                task_queue=task_queue,
            )

        assert wf_result["ingestion_status"] == "indexed"
        assert wf_result["lifecycle_status"] == "draft"
        assert wf_result["progress_pct"] == 100
        assert wf_result["page_count"] == 1
        assert wf_result["chunk_count"] == 2
        assert wf_result["newly_embedded"] == 2

        # Verify persisted pages, chunks, and unit-norm 768D embeddings under RLS
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            pages = (
                (
                    await session.execute(
                        select(PolicyDocumentPage).where(PolicyDocumentPage.document_id == doc_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(pages) == 1
            assert pages[0].language == "mixed"

            chunks = (
                (
                    await session.execute(
                        select(PolicyChunk)
                        .where(PolicyChunk.document_id == doc_id)
                        .order_by(PolicyChunk.chunk_index.asc())
                    )
                )
                .scalars()
                .all()
            )
            assert len(chunks) == 2
            assert {c.language for c in chunks} == {"en", "ur"}

            embs = (
                (
                    await session.execute(
                        select(PolicyChunkEmbedding).where(
                            PolicyChunkEmbedding.chunk_id.in_([c.id for c in chunks])
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(embs) == 2
            for emb in embs:
                assert emb.model == "gemini-embedding-001"
                assert emb.dim == 768
                norm = math.sqrt(sum(v * v for v in emb.embedding))
                assert math.isclose(norm, 1.0, rel_tol=1e-5)
    finally:
        for key in stored_keys:
            StorageHelper.delete_local_file(tenant_id=tenant_id, storage_key=key)
        if created_doc_ids:
            admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
            try:
                await admin_conn.execute(
                    "DELETE FROM public.policy_documents WHERE id = ANY($1::uuid[]);",
                    created_doc_ids,
                )
            finally:
                await admin_conn.close()
