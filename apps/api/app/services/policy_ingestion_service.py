"""
Idempotent 4-Step Policy Ingestion Service for Agentic HR Core (Stage 5 Prompt 3, ADR 008).

Executes:
1. `parse_and_store_pages`: Reads stored file bytes via `StorageHelper`, runs `LayeredPolicyParser`,
   persists `policy_document_pages`, sets `progress_pct=30, progress_stage="parsed"`.
2. `chunk_and_index_lexical`: Runs `chunk_policy_document`, diff-preserves unchanged
   `(chunk_index, content_hash)` `PolicyChunk` rows (so same-document re-runs keep their
   existing embeddings), populates PostgreSQL `to_tsvector('simple', normalized_text)`,
   sets `progress_pct=60, progress_stage="chunked"`.
3. `embed_chunks_idempotent`: Reuses existing embeddings by `(tenant_id, model, dim, content_hash)`
   across retries and prior policy versions (v1 -> v2), calling `ModelGateway.embed_texts`
   (`task_type="RETRIEVAL_DOCUMENT"`) ONLY for unseen chunk hashes. Sets `progress_pct=90,
   progress_stage="embedded"`.
4. `finalize_ingestion`: Transitions `processing -> needs_review` if any page requires OCR or
   failed extraction, else `processing -> indexed` (`progress_pct=100`).
   NEVER sets `lifecycle_status="active"` (`lifecycle_status` stays `"draft"` until Stage 2
   `policy.publish` approval completes).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.model_gateway import model_gateway
from app.models.policy_chunk import PolicyChunk
from app.models.policy_chunk_embedding import PolicyChunkEmbedding
from app.models.policy_document import PolicyDocument
from app.models.policy_document_page import PolicyDocumentPage
from app.models.tenant import set_tenant_context
from app.services.audit_service import write_audit
from app.services.policy_chunker import chunk_policy_document
from app.services.policy_lifecycle import (
    PolicyIngestionStatus,
    assert_ingestion_transition,
)
from app.services.policy_parser import (
    LayeredPolicyParser,
    ParsedPolicyDocument,
    ParsedPolicyPage,
)
from app.services.storage_helper import StorageHelper
from app.services.text_normalizer import detect_prompt_injection_signals


class PolicyDocumentNotFoundError(ValueError):
    """Raised when a target `policy_documents` record does not exist in the active tenant."""


class PolicyIngestionService:
    """Tenant-scoped, idempotent 4-step policy ingestion pipeline."""

    @staticmethod
    async def _load_document(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        document_id: UUID,
    ) -> PolicyDocument:
        await set_tenant_context(session, str(tenant_id))
        doc = (
            await session.execute(
                select(PolicyDocument).where(
                    PolicyDocument.tenant_id == tenant_id,
                    PolicyDocument.id == document_id,
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            raise PolicyDocumentNotFoundError(
                f"PolicyDocument '{document_id}' not found for tenant '{tenant_id}'."
            )
        return doc

    @classmethod
    async def parse_and_store_pages(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        document_id: UUID | str,
    ) -> dict[str, Any]:
        """
        Step 1: Loads raw file via `StorageHelper`, runs `LayeredPolicyParser`, stores
        `policy_document_pages`, and updates `progress_pct=30, progress_stage='parsed'`.
        Idempotent on Activity retry if `ingestion_status` is already `'processing'`.
        """
        t_id = UUID(str(tenant_id))
        d_id = UUID(str(document_id))
        doc = await cls._load_document(session, tenant_id=t_id, document_id=d_id)

        curr = doc.ingestion_status
        if curr == PolicyIngestionStatus.PROCESSING.value:
            # Idempotent retry of Step 1 when Temporal retries Activity 1
            pass
        elif curr == PolicyIngestionStatus.QUEUED.value:
            doc.ingestion_status = assert_ingestion_transition(
                curr,
                PolicyIngestionStatus.PROCESSING.value,
            )
        else:
            # queued_stub, indexed, needs_review, or failed -> queued -> processing
            queued_state = assert_ingestion_transition(curr, PolicyIngestionStatus.QUEUED.value)
            doc.ingestion_status = assert_ingestion_transition(
                queued_state,
                PolicyIngestionStatus.PROCESSING.value,
            )

        doc.progress_pct = 10
        doc.progress_stage = "parsing"
        doc.error_message = None
        await session.flush()

        raw_bytes = await StorageHelper.read_file(
            tenant_id=t_id,
            storage_provider=doc.storage_provider,
            storage_key=doc.storage_key,
            session=session,
        )

        parser = LayeredPolicyParser()
        parsed = parser.parse(
            raw_bytes,
            file_name=doc.file_name,
            mime_type=doc.mime_type,
        )

        # Replace any prior pages for this document_id idempotently
        await session.execute(
            delete(PolicyDocumentPage).where(
                PolicyDocumentPage.tenant_id == t_id,
                PolicyDocumentPage.document_id == d_id,
            )
        )
        await session.flush()

        for page in parsed.pages:
            session.add(
                PolicyDocumentPage(
                    tenant_id=t_id,
                    document_id=d_id,
                    page_number=page.page_number,
                    raw_text=page.raw_text,
                    cleaned_text=page.cleaned_text,
                    language=page.language,
                    quality_score=page.quality_score,
                    needs_ocr=page.needs_ocr,
                    ocr_applied=page.ocr_applied,
                    extraction_status=page.extraction_status,
                    error_message=page.error_message,
                    metadata_json=page.metadata_json,
                )
            )

        doc.page_count = parsed.page_count
        doc.low_confidence_pages = parsed.low_confidence_pages
        doc.has_prompt_injection_warning = parsed.has_prompt_injection_warning
        doc.progress_pct = 30
        doc.progress_stage = "parsed"
        await session.flush()

        return {
            "document_id": str(d_id),
            "page_count": parsed.page_count,
            "low_confidence_count": len(parsed.low_confidence_pages),
            "has_prompt_injection_warning": parsed.has_prompt_injection_warning,
            "progress_pct": doc.progress_pct,
            "progress_stage": doc.progress_stage,
        }

    @classmethod
    async def chunk_and_index_lexical(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        document_id: UUID | str,
    ) -> dict[str, Any]:
        """
        Step 2: Runs `chunk_policy_document` over stored `policy_document_pages`, diff-preserves
        existing `PolicyChunk` rows whose `(chunk_index, content_hash)` is unchanged (keeping
        same-document cached embeddings intact), populates `to_tsvector('simple', normalized_text)`,
        and sets `progress_pct=60, progress_stage='chunked'`.
        """
        t_id = UUID(str(tenant_id))
        d_id = UUID(str(document_id))
        doc = await cls._load_document(session, tenant_id=t_id, document_id=d_id)

        db_pages = (
            (
                await session.execute(
                    select(PolicyDocumentPage)
                    .where(
                        PolicyDocumentPage.tenant_id == t_id,
                        PolicyDocumentPage.document_id == d_id,
                    )
                    .order_by(PolicyDocumentPage.page_number.asc())
                )
            )
            .scalars()
            .all()
        )

        parsed_doc = ParsedPolicyDocument(
            file_name=doc.file_name,
            mime_type=doc.mime_type,
            page_count=len(db_pages),
            pages=[
                ParsedPolicyPage(
                    page_number=p.page_number,
                    raw_text=p.raw_text,
                    cleaned_text=p.cleaned_text,
                    language=p.language,  # type: ignore[arg-type]
                    quality_score=p.quality_score,
                    needs_ocr=p.needs_ocr,
                    ocr_applied=p.ocr_applied,
                    extraction_status=p.extraction_status,  # type: ignore[arg-type]
                    error_message=p.error_message,
                    metadata_json=dict(p.metadata_json or {}),
                )
                for p in db_pages
            ],
            low_confidence_pages=list(doc.low_confidence_pages or []),
            has_prompt_injection_warning=doc.has_prompt_injection_warning,
        )

        drafts = chunk_policy_document(parsed_doc, document_title=doc.title)

        existing_chunks = (
            (
                await session.execute(
                    select(PolicyChunk)
                    .where(
                        PolicyChunk.tenant_id == t_id,
                        PolicyChunk.document_id == d_id,
                    )
                    .order_by(PolicyChunk.chunk_index.asc())
                )
            )
            .scalars()
            .all()
        )
        existing_by_idx = {c.chunk_index: c for c in existing_chunks}

        preserved_count = 0
        upserted_count = 0

        for draft in drafts:
            prev = existing_by_idx.get(draft.chunk_index)
            tsv_expr = func.to_tsvector("simple", draft.normalized_text)
            if prev is not None and prev.content_hash == draft.content_hash:
                # Preserve existing chunk ID and its linked PolicyChunkEmbedding rows
                prev.version = doc.version
                prev.section_path = draft.section_path
                prev.page_start = draft.page_start
                prev.page_end = draft.page_end
                prev.page_range = draft.page_range
                prev.text = draft.text
                prev.normalized_text = draft.normalized_text
                prev.token_count = draft.token_count
                prev.language = draft.language
                prev.visibility = doc.visibility
                prev.tsv = tsv_expr
                prev.has_injection_flag = draft.has_injection_flag
                prev.metadata_json = draft.metadata_json
                preserved_count += 1
            elif prev is not None:
                # Content changed at this chunk_index: remove stale embeddings for this chunk_id
                await session.execute(
                    delete(PolicyChunkEmbedding).where(
                        PolicyChunkEmbedding.tenant_id == t_id,
                        PolicyChunkEmbedding.chunk_id == prev.id,
                    )
                )
                prev.version = doc.version
                prev.section_path = draft.section_path
                prev.page_start = draft.page_start
                prev.page_end = draft.page_end
                prev.page_range = draft.page_range
                prev.text = draft.text
                prev.normalized_text = draft.normalized_text
                prev.content_hash = draft.content_hash
                prev.token_count = draft.token_count
                prev.language = draft.language
                prev.visibility = doc.visibility
                prev.tsv = tsv_expr
                prev.has_injection_flag = draft.has_injection_flag
                prev.metadata_json = draft.metadata_json
                upserted_count += 1
            else:
                session.add(
                    PolicyChunk(
                        tenant_id=t_id,
                        document_id=d_id,
                        version=doc.version,
                        chunk_index=draft.chunk_index,
                        section_path=draft.section_path,
                        page_start=draft.page_start,
                        page_end=draft.page_end,
                        page_range=draft.page_range,
                        text=draft.text,
                        normalized_text=draft.normalized_text,
                        content_hash=draft.content_hash,
                        token_count=draft.token_count,
                        language=draft.language,
                        visibility=doc.visibility,
                        tsv=tsv_expr,
                        has_injection_flag=draft.has_injection_flag,
                        metadata_json=draft.metadata_json,
                    )
                )
                upserted_count += 1

        # Remove any trailing chunks if the document shrank
        if len(existing_chunks) > len(drafts):
            await session.execute(
                delete(PolicyChunk).where(
                    PolicyChunk.tenant_id == t_id,
                    PolicyChunk.document_id == d_id,
                    PolicyChunk.chunk_index >= len(drafts),
                )
            )

        if any(d.has_injection_flag for d in drafts):
            doc.has_prompt_injection_warning = True

        doc.progress_pct = 60
        doc.progress_stage = "chunked"
        await session.flush()

        return {
            "document_id": str(d_id),
            "chunk_count": len(drafts),
            "preserved_chunks": preserved_count,
            "upserted_chunks": upserted_count,
            "progress_pct": doc.progress_pct,
            "progress_stage": doc.progress_stage,
        }

    @classmethod
    async def embed_chunks_idempotent(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        document_id: UUID | str,
        model: str | None = None,
        dim: int | None = None,
    ) -> dict[str, Any]:
        """
        Step 3: Queries `policy_chunk_embeddings` for existing `(tenant_id, model, dim, content_hash)`
        vectors (from retries OR prior versions of the policy), reuses cached embeddings with
        0 duplicate API calls, and calls `ModelGateway.embed_texts` (`task_type="RETRIEVAL_DOCUMENT"`)
        ONLY for newly changed chunk hashes. Updates `progress_pct=90, progress_stage='embedded'`.
        """
        t_id = UUID(str(tenant_id))
        d_id = UUID(str(document_id))
        doc = await cls._load_document(session, tenant_id=t_id, document_id=d_id)

        target_model = model_gateway.resolve_embedding_model(model)
        target_dim = int(dim or settings.EMBEDDING_DIM or 768)

        chunks = (
            (
                await session.execute(
                    select(PolicyChunk)
                    .where(
                        PolicyChunk.tenant_id == t_id,
                        PolicyChunk.document_id == d_id,
                    )
                    .order_by(PolicyChunk.chunk_index.asc())
                )
            )
            .scalars()
            .all()
        )

        if not chunks:
            doc.progress_pct = 90
            doc.progress_stage = "embedded"
            await session.flush()
            return {
                "document_id": str(d_id),
                "total_chunks": 0,
                "already_embedded": 0,
                "reused_from_cache": 0,
                "newly_embedded": 0,
                "progress_pct": doc.progress_pct,
                "progress_stage": doc.progress_stage,
            }

        chunk_ids = [c.id for c in chunks]
        existing_for_doc = (
            (
                await session.execute(
                    select(PolicyChunkEmbedding).where(
                        PolicyChunkEmbedding.tenant_id == t_id,
                        PolicyChunkEmbedding.chunk_id.in_(chunk_ids),
                        PolicyChunkEmbedding.model == target_model,
                        PolicyChunkEmbedding.dim == target_dim,
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_by_chunk_id = {e.chunk_id: e for e in existing_for_doc}

        already_embedded = 0
        pending_chunks: list[PolicyChunk] = []

        for ch in chunks:
            curr_emb = existing_by_chunk_id.get(ch.id)
            if curr_emb is not None and curr_emb.content_hash == ch.content_hash:
                already_embedded += 1
            else:
                if curr_emb is not None:
                    await session.delete(curr_emb)
                pending_chunks.append(ch)

        await session.flush()

        reused_from_cache = 0
        newly_embedded = 0

        if pending_chunks:
            needed_hashes = list({c.content_hash for c in pending_chunks})
            cached_rows = (
                (
                    await session.execute(
                        select(PolicyChunkEmbedding).where(
                            PolicyChunkEmbedding.tenant_id == t_id,
                            PolicyChunkEmbedding.model == target_model,
                            PolicyChunkEmbedding.dim == target_dim,
                            PolicyChunkEmbedding.content_hash.in_(needed_hashes),
                        )
                    )
                )
                .scalars()
                .all()
            )

            cached_vec_by_hash: dict[str, list[float]] = {}
            for row in cached_rows:
                if row.content_hash not in cached_vec_by_hash:
                    cached_vec_by_hash[row.content_hash] = list(row.embedding)

            unseen_hashes: list[str] = []
            unseen_texts: list[str] = []
            seen_unseen_set: set[str] = set()

            for ch in pending_chunks:
                if (
                    ch.content_hash not in cached_vec_by_hash
                    and ch.content_hash not in seen_unseen_set
                ):
                    seen_unseen_set.add(ch.content_hash)
                    unseen_hashes.append(ch.content_hash)
                    unseen_texts.append(f"{ch.section_path}\n{ch.text}")

            new_vec_by_hash: dict[str, list[float]] = {}
            if unseen_texts:
                batch_res = await model_gateway.embed_texts(
                    unseen_texts,
                    task_type="RETRIEVAL_DOCUMENT",
                    tenant_id=str(t_id),
                    model=target_model,
                    dim=target_dim,
                )
                for c_hash, vec in zip(unseen_hashes, batch_res.embeddings, strict=True):
                    new_vec_by_hash[c_hash] = vec

            for ch in pending_chunks:
                if ch.content_hash in cached_vec_by_hash:
                    vec = cached_vec_by_hash[ch.content_hash]
                    reused_from_cache += 1
                else:
                    vec = new_vec_by_hash[ch.content_hash]
                    newly_embedded += 1

                session.add(
                    PolicyChunkEmbedding(
                        tenant_id=t_id,
                        chunk_id=ch.id,
                        content_hash=ch.content_hash,
                        model=target_model,
                        dim=target_dim,
                        embedding=vec,
                    )
                )

        doc.progress_pct = 90
        doc.progress_stage = "embedded"
        await session.flush()

        return {
            "document_id": str(d_id),
            "total_chunks": len(chunks),
            "already_embedded": already_embedded,
            "reused_from_cache": reused_from_cache,
            "newly_embedded": newly_embedded,
            "model": target_model,
            "dim": target_dim,
            "progress_pct": doc.progress_pct,
            "progress_stage": doc.progress_stage,
        }

    @classmethod
    async def finalize_ingestion(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        document_id: UUID | str,
    ) -> dict[str, Any]:
        """
        Step 4: Sets `ingestion_status='needs_review'` if any page has `needs_ocr=True` or
        `extraction_status='failed'` (or `page_count == 0`), else sets `ingestion_status='indexed'`.
        Updates `progress_pct=100`. NEVER alters `lifecycle_status` (`'draft'` stays `'draft'`).
        """
        t_id = UUID(str(tenant_id))
        d_id = UUID(str(document_id))
        doc = await cls._load_document(session, tenant_id=t_id, document_id=d_id)

        db_pages = (
            (
                await session.execute(
                    select(PolicyDocumentPage)
                    .where(
                        PolicyDocumentPage.tenant_id == t_id,
                        PolicyDocumentPage.document_id == d_id,
                    )
                    .order_by(PolicyDocumentPage.page_number.asc())
                )
            )
            .scalars()
            .all()
        )

        has_review_pages = len(db_pages) == 0 or any(
            p.needs_ocr or p.extraction_status != "ok" for p in db_pages
        )

        # Refresh low_confidence_pages and prompt-injection flag from authoritative DB pages
        low_conf: list[dict[str, Any]] = []
        injection_found = bool(doc.has_prompt_injection_warning)
        for p in db_pages:
            if p.needs_ocr or p.extraction_status != "ok":
                reasons = (p.metadata_json or {}).get("quality_reasons", [])
                low_conf.append(
                    {
                        "page_number": p.page_number,
                        "quality_score": p.quality_score,
                        "reasons": reasons,
                        "extraction_status": p.extraction_status,
                    }
                )
            if detect_prompt_injection_signals(p.raw_text) or detect_prompt_injection_signals(
                p.cleaned_text
            ):
                injection_found = True

        doc.low_confidence_pages = low_conf
        doc.has_prompt_injection_warning = injection_found

        target_status = (
            PolicyIngestionStatus.NEEDS_REVIEW.value
            if has_review_pages
            else PolicyIngestionStatus.INDEXED.value
        )

        if doc.ingestion_status != target_status:
            doc.ingestion_status = assert_ingestion_transition(
                doc.ingestion_status,
                target_status,
            )

        doc.progress_pct = 100
        doc.progress_stage = target_status
        doc.error_message = None
        await session.flush()

        await write_audit(
            session,
            action="policy_document.ingestion_completed",
            target_type="policy_document",
            target_id=doc.id,
            details={
                "title": doc.title,
                "version": doc.version,
                "ingestion_status": doc.ingestion_status,
                "lifecycle_status": doc.lifecycle_status,
                "page_count": doc.page_count,
                "low_confidence_count": len(low_conf),
                "has_prompt_injection_warning": doc.has_prompt_injection_warning,
            },
            actor_id=doc.uploaded_by,
            tenant_id=t_id,
        )

        return {
            "document_id": str(d_id),
            "ingestion_status": doc.ingestion_status,
            "lifecycle_status": doc.lifecycle_status,
            "progress_pct": doc.progress_pct,
            "progress_stage": doc.progress_stage,
            "page_count": doc.page_count,
            "low_confidence_pages": low_conf,
            "has_prompt_injection_warning": doc.has_prompt_injection_warning,
        }

    @classmethod
    async def mark_ingestion_failed(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        document_id: UUID | str,
        error_message: str,
    ) -> dict[str, Any]:
        """
        Transitions `ingestion_status` to `'failed'`, stores a concise `error_message`,
        and emits a `policy_document.ingestion_failed` audit log entry.
        """
        t_id = UUID(str(tenant_id))
        d_id = UUID(str(document_id))
        doc = await cls._load_document(session, tenant_id=t_id, document_id=d_id)

        concise_err = (error_message or "Policy ingestion failed.").strip()[:1000]
        curr = doc.ingestion_status
        if curr != PolicyIngestionStatus.FAILED.value:
            if curr != PolicyIngestionStatus.PROCESSING.value:
                if curr != PolicyIngestionStatus.QUEUED.value:
                    curr = assert_ingestion_transition(curr, PolicyIngestionStatus.QUEUED.value)
                curr = assert_ingestion_transition(curr, PolicyIngestionStatus.PROCESSING.value)
            doc.ingestion_status = assert_ingestion_transition(
                curr,
                PolicyIngestionStatus.FAILED.value,
            )

        doc.progress_stage = "failed"
        doc.error_message = concise_err
        await session.flush()

        await write_audit(
            session,
            action="policy_document.ingestion_failed",
            target_type="policy_document",
            target_id=doc.id,
            details={
                "title": doc.title,
                "version": doc.version,
                "ingestion_status": doc.ingestion_status,
                "lifecycle_status": doc.lifecycle_status,
                "error_message": concise_err,
            },
            actor_id=doc.uploaded_by,
            tenant_id=t_id,
        )

        return {
            "document_id": str(d_id),
            "ingestion_status": doc.ingestion_status,
            "lifecycle_status": doc.lifecycle_status,
            "progress_stage": doc.progress_stage,
            "error_message": doc.error_message,
        }


async def run_policy_ingestion_inline(
    db: AsyncSession,
    tenant_id: UUID | str,
    document_id: UUID | str,
) -> dict[str, Any]:
    """
    Executes Steps 1-4 of `PolicyIngestionService` synchronously in order inside `db`.
    On unrecoverable error, marks the document `ingestion_status='failed'` with a concise
    `error_message` and emits an audit log.
    """
    t_id = UUID(str(tenant_id))
    d_id = UUID(str(document_id))
    try:
        step1 = await PolicyIngestionService.parse_and_store_pages(
            db,
            tenant_id=t_id,
            document_id=d_id,
        )
        step2 = await PolicyIngestionService.chunk_and_index_lexical(
            db,
            tenant_id=t_id,
            document_id=d_id,
        )
        step3 = await PolicyIngestionService.embed_chunks_idempotent(
            db,
            tenant_id=t_id,
            document_id=d_id,
        )
        step4 = await PolicyIngestionService.finalize_ingestion(
            db,
            tenant_id=t_id,
            document_id=d_id,
        )
        await db.commit()
        return {
            **step4,
            "chunk_count": step2["chunk_count"],
            "already_embedded": step3["already_embedded"],
            "reused_from_cache": step3["reused_from_cache"],
            "newly_embedded": step3["newly_embedded"],
            "page_count": step1["page_count"],
        }
    except PolicyDocumentNotFoundError:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        failed_summary = await PolicyIngestionService.mark_ingestion_failed(
            db,
            tenant_id=t_id,
            document_id=d_id,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        await db.commit()
        return failed_summary
