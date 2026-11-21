"""
Stage 5 Policy Document Lifecycle Management Service for Agentic HR Core (Prompt 4).

Provides:
- Versioned policy uploads (`POST /api/v1/policies`) with magic-byte validation, orphan file
  cleanup, `policy.uploaded` outbox event, and inline or Temporal `PolicyIngestionWorkflow` dispatch.
- RBAC-filtered policy library listing (`all` | `managers` | `hr_only`) and OCR review queue.
- Document preview (`pages` + `chunks`), draft chunk editing + single-chunk re-embedding,
  manual OCR page repair (`fix_page`), and re-ingestion.
- Stage 2 `policy.publish` approval workflow integration (draft -> review -> active).
- Soft-delete archival (`lifecycle_status='archived'`) removing embeddings from search while
  preserving version & audit history.
"""

import hashlib
import logging
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.model_gateway import model_gateway
from app.core.security import AuthenticatedUserContext
from app.models.connector import ConnectorCredential
from app.models.policy_chunk import PolicyChunk
from app.models.policy_chunk_embedding import PolicyChunkEmbedding
from app.models.policy_document import PolicyDocument
from app.models.policy_document_page import PolicyDocumentPage
from app.models.tenant import set_tenant_context
from app.schemas.approval import ApprovalCreateRequest
from app.schemas.policy import (
    PolicyArchiveResponse,
    PolicyChunkPreviewItem,
    PolicyChunkUpdateRequest,
    PolicyDocumentResponse,
    PolicyPageFixRequest,
    PolicyPagePreviewItem,
    PolicyPreviewResponse,
    PolicyPublishResponse,
    PolicyReviewQueueItem,
)
from app.services.approval_service import create_approval
from app.services.audit_service import write_audit
from app.services.outbox_service import enqueue_outbox_event
from app.services.policy_chunker import estimate_token_count
from app.services.policy_ingestion_service import (
    PolicyIngestionService,
    run_policy_ingestion_inline,
)
from app.services.policy_lifecycle import (
    PolicyIngestionStatus,
    PolicyLifecycleStatus,
    PolicyTransitionError,
    assert_ingestion_transition,
    assert_lifecycle_transition,
)
from app.services.storage_helper import (
    MAX_UPLOAD_SIZE_BYTES,
    StorageHelper,
    validate_file_magic_bytes,
)
from app.services.text_normalizer import (
    detect_language,
    detect_prompt_injection_signals,
    normalize_bilingual_text,
    normalize_for_search,
)

logger = logging.getLogger("agentic_hr.services.policy")

ALLOWED_POLICY_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
ALLOWED_POLICY_MIME_TYPES: set[str] = set(ALLOWED_POLICY_EXTENSIONS.values())
ALLOWED_DOC_TYPES: frozenset[str] = frozenset(
    {"company_policy", "labour_law", "helpdesk_knowledge"}
)
ALLOWED_VISIBILITIES: frozenset[str] = frozenset({"all", "managers", "hr_only"})
ALLOWED_LANGUAGES: frozenset[str] = frozenset({"en", "ur", "mixed"})


def _is_hr_admin(user: AuthenticatedUserContext) -> bool:
    return user.role == "owner" or bool(user.is_hr_head)


def _allowed_visibilities_for_user(user: AuthenticatedUserContext) -> tuple[str, ...]:
    if _is_hr_admin(user):
        return ("all", "managers", "hr_only")
    if user.role == "manager":
        return ("all", "managers")
    return ("all",)


class PolicyService:
    """Authoritative Stage 5 policy lifecycle service."""

    @staticmethod
    def _validate_and_resolve_mime(file_name: str, content_type: str | None) -> str:
        ext = Path(file_name or "").suffix.lower()
        clean_mime = (content_type or "").split(";")[0].strip().lower()

        if clean_mime in ALLOWED_POLICY_MIME_TYPES:
            if ext and ext not in ALLOWED_POLICY_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"INVALID_POLICY_FILE_TYPE: Extension '{ext}' is not supported. "
                        "Allowed formats: PDF, DOCX, TXT."
                    ),
                )
            return clean_mime

        if ext in ALLOWED_POLICY_EXTENSIONS and clean_mime in ("", "application/octet-stream"):
            return ALLOWED_POLICY_EXTENSIONS[ext]

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"INVALID_POLICY_FILE_TYPE: File '{file_name}' ({content_type}) is not supported. "
                "Allowed formats: PDF, DOCX, TXT."
            ),
        )

    @staticmethod
    async def _resolve_storage_provider(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        requested_provider: str,
    ) -> str:
        prov = (requested_provider or "local").strip().lower()
        if prov in ("local", "google_drive"):
            return prov
        if prov != "auto":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported storage_provider '{requested_provider}'. "
                    "Expected 'auto', 'local', or 'google_drive'."
                ),
            )

        cred = (
            await session.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.tenant_id == tenant_id,
                    ConnectorCredential.provider == "google",
                    ConnectorCredential.status == "connected",
                )
            )
        ).scalar_one_or_none()
        if (
            cred is not None
            and not cred.is_read_only
            and any("drive.file" in s for s in (cred.scopes or []))
        ):
            return "google_drive"
        return "local"

    @staticmethod
    async def _trigger_ingestion(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        document_id: UUID,
        actor_clerk_id: str | None,
    ) -> None:
        """
        Triggers ingestion inline (`INGEST_INLINE=true` or `ENVIRONMENT=test`) or starts
        Temporal `PolicyIngestionWorkflow` asynchronously when Temporal is reachable.
        """
        if settings.should_ingest_inline:
            await run_policy_ingestion_inline(session, tenant_id, document_id)
            await set_tenant_context(session, str(tenant_id), actor_clerk_id)
            return

        try:
            from app.core.temporal import get_temporal_client
            from app.workflows import PolicyIngestionWorkflow

            client = await get_temporal_client()
            await client.start_workflow(
                PolicyIngestionWorkflow.run,
                args=[str(tenant_id), str(document_id)],
                id=f"policy-ingest-{document_id}",
                task_queue=settings.TEMPORAL_TASK_QUEUE,
            )
        except Exception as exc:
            logger.warning(
                f"Temporal PolicyIngestionWorkflow start skipped for {document_id}: {exc}"
            )

    @staticmethod
    async def _get_tenant_document_or_404(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        policy_id: UUID,
    ) -> PolicyDocument:
        doc = (
            await session.execute(
                select(PolicyDocument).where(
                    PolicyDocument.tenant_id == tenant_id,
                    PolicyDocument.id == policy_id,
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Policy document '{policy_id}' not found.",
            )
        return doc

    @classmethod
    async def upload_policy(
        cls,
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
        file_name: str,
        content: bytes,
        content_type: str | None,
        title: str | None = None,
        doc_type: str = "company_policy",
        visibility: str = "all",
        language: str | None = None,
        effective_date: date | None = None,
        source: str = "upload",
        storage_provider: str = "local",
    ) -> PolicyDocumentResponse:
        """
        Uploads a policy file (PDF/DOCX/TXT <= 10MB) with version creation (never overwrites),
        magic-byte validation, orphan file cleanup on DB failure, `policy.uploaded` outbox event,
        and inline/Temporal ingestion dispatch.
        """
        t_id = UUID(str(current_user.tenant_id))
        actor_uuid = UUID(str(current_user.user_id))

        clean_file_name = (file_name or "").strip()
        if not clean_file_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file must have a valid filename.",
            )

        clean_doc_type = (doc_type or "company_policy").strip().lower()
        if clean_doc_type not in ALLOWED_DOC_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid doc_type '{doc_type}'. Allowed: {sorted(ALLOWED_DOC_TYPES)}.",
            )

        clean_visibility = (visibility or "all").strip().lower()
        if clean_visibility not in ALLOWED_VISIBILITIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid visibility '{visibility}'. Allowed: {sorted(ALLOWED_VISIBILITIES)}.",
            )

        if language is not None and language.strip().lower() not in ALLOWED_LANGUAGES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid language '{language}'. Allowed: {sorted(ALLOWED_LANGUAGES)}.",
            )

        resolved_mime = cls._validate_and_resolve_mime(clean_file_name, content_type)
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded policy file cannot be empty.",
            )
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"POLICY_FILE_TOO_LARGE: File size ({len(content)} bytes) exceeds "
                    f"maximum allowed limit of {MAX_UPLOAD_SIZE_BYTES} bytes (10MB)."
                ),
            )

        validate_file_magic_bytes(
            content,
            resolved_mime,
            error_code="INVALID_POLICY_FILE_TYPE",
        )

        resolved_provider = await cls._resolve_storage_provider(
            session,
            tenant_id=t_id,
            requested_provider=storage_provider,
        )

        try:
            stored = await StorageHelper.store_file(
                tenant_id=t_id,
                category="policies",
                file_name=clean_file_name,
                content=content,
                mime_type=resolved_mime,
                storage_provider=resolved_provider,
                session=session,
                actor_id=actor_uuid,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        resolved_title = (
            (title or "").strip() or Path(clean_file_name).stem.replace("_", " ").strip()
        )[:255]
        clean_source = (source or "upload").strip()[:100] or "upload"

        try:
            existing_versions = (
                (
                    await session.execute(
                        select(PolicyDocument)
                        .where(
                            PolicyDocument.tenant_id == t_id,
                            func.lower(PolicyDocument.title) == resolved_title.lower(),
                        )
                        .order_by(desc(PolicyDocument.version))
                    )
                )
                .scalars()
                .all()
            )
            if existing_versions:
                next_version = existing_versions[0].version + 1
                active_match = next(
                    (r for r in existing_versions if r.lifecycle_status == "active"),
                    existing_versions[0],
                )
                supersedes_id = active_match.id
            else:
                next_version = 1
                supersedes_id = None

            doc = PolicyDocument(
                tenant_id=t_id,
                title=resolved_title,
                doc_type=clean_doc_type,
                visibility=clean_visibility,
                version=next_version,
                file_name=clean_file_name[:255],
                mime_type=resolved_mime,
                size_bytes=stored.size_bytes,
                storage_provider=stored.storage_provider,
                storage_key=stored.storage_key,
                ingestion_status=PolicyIngestionStatus.QUEUED.value,
                lifecycle_status=PolicyLifecycleStatus.DRAFT.value,
                effective_date=effective_date,
                source=clean_source,
                supersedes_document_id=supersedes_id,
                progress_pct=0,
                progress_stage="queued",
                uploaded_by=actor_uuid,
            )
            session.add(doc)
            await session.flush()

            await enqueue_outbox_event(
                session=session,
                tenant_id=t_id,
                event_type="policy.uploaded",
                event_payload={
                    "policy_document_id": str(doc.id),
                    "title": doc.title,
                    "version": doc.version,
                    "doc_type": doc.doc_type,
                    "visibility": doc.visibility,
                    "file_name": doc.file_name,
                    "mime_type": doc.mime_type,
                    "size_bytes": doc.size_bytes,
                    "storage_provider": doc.storage_provider,
                    "storage_key": doc.storage_key,
                    "ingestion_status": doc.ingestion_status,
                    "lifecycle_status": doc.lifecycle_status,
                    "requested_language": language,
                },
            )

            await write_audit(
                session,
                action="policy_document.uploaded",
                target_type="policy_document",
                target_id=doc.id,
                details={
                    "title": doc.title,
                    "version": doc.version,
                    "doc_type": doc.doc_type,
                    "visibility": doc.visibility,
                    "file_name": doc.file_name,
                    "mime_type": doc.mime_type,
                    "size_bytes": doc.size_bytes,
                    "storage_provider": doc.storage_provider,
                    "ingestion_status": doc.ingestion_status,
                    "supersedes_document_id": str(supersedes_id) if supersedes_id else None,
                },
                actor_id=actor_uuid,
                tenant_id=t_id,
            )

            doc_id = doc.id
            await session.commit()
        except Exception:
            if stored.storage_provider == "local":
                StorageHelper.delete_local_file(tenant_id=t_id, storage_key=stored.storage_key)
            raise

        await set_tenant_context(session, str(t_id), current_user.clerk_user_id)
        await cls._trigger_ingestion(
            session,
            tenant_id=t_id,
            document_id=doc_id,
            actor_clerk_id=current_user.clerk_user_id,
        )

        await set_tenant_context(session, str(t_id), current_user.clerk_user_id)
        refreshed = await cls._get_tenant_document_or_404(
            session,
            tenant_id=t_id,
            policy_id=doc_id,
        )
        return PolicyDocumentResponse.model_validate(refreshed)

    @staticmethod
    async def list_policies(
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
        doc_type: str | None = None,
        visibility: str | None = None,
        lifecycle_status: str | None = None,
        ingestion_status: str | None = None,
        language: str | None = None,
        needs_review_only: bool = False,
        include_archived: bool = False,
    ) -> list[PolicyDocumentResponse]:
        """Lists tenant policy documents with RBAC visibility and lifecycle filtering."""
        t_id = UUID(str(current_user.tenant_id))
        stmt = select(PolicyDocument).where(PolicyDocument.tenant_id == t_id)

        if not _is_hr_admin(current_user):
            allowed_vis = _allowed_visibilities_for_user(current_user)
            stmt = stmt.where(
                PolicyDocument.lifecycle_status == PolicyLifecycleStatus.ACTIVE.value,
                PolicyDocument.visibility.in_(allowed_vis),
            )
        else:
            if not include_archived and lifecycle_status != PolicyLifecycleStatus.ARCHIVED.value:
                stmt = stmt.where(
                    PolicyDocument.lifecycle_status != PolicyLifecycleStatus.ARCHIVED.value
                )
            if lifecycle_status:
                stmt = stmt.where(PolicyDocument.lifecycle_status == lifecycle_status)

        if doc_type:
            stmt = stmt.where(PolicyDocument.doc_type == doc_type)
        if visibility:
            stmt = stmt.where(PolicyDocument.visibility == visibility)
        if ingestion_status:
            stmt = stmt.where(PolicyDocument.ingestion_status == ingestion_status)
        if needs_review_only:
            stmt = stmt.where(
                PolicyDocument.ingestion_status.in_(
                    (
                        PolicyIngestionStatus.NEEDS_REVIEW.value,
                        PolicyIngestionStatus.FAILED.value,
                    )
                )
            )
        if language:
            lang_doc_ids = (
                select(PolicyDocumentPage.document_id)
                .where(
                    PolicyDocumentPage.tenant_id == t_id,
                    PolicyDocumentPage.language == language,
                )
                .distinct()
            )
            stmt = stmt.where(PolicyDocument.id.in_(lang_doc_ids))

        stmt = stmt.order_by(desc(PolicyDocument.created_at), desc(PolicyDocument.version))
        rows = (await session.execute(stmt)).scalars().all()
        return [PolicyDocumentResponse.model_validate(r) for r in rows]

    @classmethod
    async def list_review_queue(
        cls,
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
    ) -> list[PolicyReviewQueueItem]:
        """Lists non-archived documents requiring OCR review or ingestion repair."""
        t_id = UUID(str(current_user.tenant_id))
        rows = (
            (
                await session.execute(
                    select(PolicyDocument)
                    .where(
                        PolicyDocument.tenant_id == t_id,
                        PolicyDocument.lifecycle_status != PolicyLifecycleStatus.ARCHIVED.value,
                        PolicyDocument.ingestion_status.in_(
                            (
                                PolicyIngestionStatus.NEEDS_REVIEW.value,
                                PolicyIngestionStatus.FAILED.value,
                            )
                        ),
                    )
                    .order_by(desc(PolicyDocument.updated_at))
                )
            )
            .scalars()
            .all()
        )
        return [
            PolicyReviewQueueItem(
                document=PolicyDocumentResponse.model_validate(r),
                low_confidence_count=len(r.low_confidence_pages or []),
            )
            for r in rows
        ]

    @classmethod
    async def get_preview(
        cls,
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
        policy_id: UUID,
    ) -> PolicyPreviewResponse:
        """Returns document metadata, extracted pages, and indexed chunks for a policy."""
        t_id = UUID(str(current_user.tenant_id))
        doc = await cls._get_tenant_document_or_404(session, tenant_id=t_id, policy_id=policy_id)

        if not _is_hr_admin(current_user):
            allowed_vis = _allowed_visibilities_for_user(current_user)
            if (
                doc.lifecycle_status != PolicyLifecycleStatus.ACTIVE.value
                or doc.visibility not in allowed_vis
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Policy document '{policy_id}' not found.",
                )

        pages = (
            (
                await session.execute(
                    select(PolicyDocumentPage)
                    .where(
                        PolicyDocumentPage.tenant_id == t_id,
                        PolicyDocumentPage.document_id == doc.id,
                    )
                    .order_by(PolicyDocumentPage.page_number.asc())
                )
            )
            .scalars()
            .all()
        )
        chunks = (
            (
                await session.execute(
                    select(PolicyChunk)
                    .where(
                        PolicyChunk.tenant_id == t_id,
                        PolicyChunk.document_id == doc.id,
                    )
                    .order_by(PolicyChunk.chunk_index.asc())
                )
            )
            .scalars()
            .all()
        )

        return PolicyPreviewResponse(
            document=PolicyDocumentResponse.model_validate(doc),
            pages=[PolicyPagePreviewItem.model_validate(p) for p in pages],
            chunks=[PolicyChunkPreviewItem.model_validate(c) for c in chunks],
        )

    @classmethod
    async def update_chunk(
        cls,
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
        policy_id: UUID,
        chunk_id: UUID,
        payload: PolicyChunkUpdateRequest,
    ) -> PolicyChunkPreviewItem:
        """
        Updates a chunk inside a `draft` policy document, re-normalizes with `normalize_for_search`,
        recomputes `content_hash`, `token_count`, `tsv`, `language`, and `has_injection_flag`,
        re-embeds ONLY that chunk via `ModelGateway`, and recalculates `doc.has_prompt_injection_warning`.
        """
        t_id = UUID(str(current_user.tenant_id))
        doc = await cls._get_tenant_document_or_404(session, tenant_id=t_id, policy_id=policy_id)

        if doc.lifecycle_status != PolicyLifecycleStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot edit chunks while policy lifecycle_status is '{doc.lifecycle_status}'. "
                    "Only 'draft' policies may be edited."
                ),
            )

        chunk = (
            await session.execute(
                select(PolicyChunk).where(
                    PolicyChunk.tenant_id == t_id,
                    PolicyChunk.document_id == doc.id,
                    PolicyChunk.id == chunk_id,
                )
            )
        ).scalar_one_or_none()
        if chunk is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chunk '{chunk_id}' not found on policy '{policy_id}'.",
            )

        cleaned_text = normalize_bilingual_text(payload.text)
        if not cleaned_text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Chunk text cannot be empty after normalization.",
            )

        new_section_path = (
            payload.section_path.strip() if payload.section_path else chunk.section_path
        )
        normalized = normalize_for_search(cleaned_text)
        new_hash = hashlib.sha256(f"{new_section_path}\n{normalized}".encode()).hexdigest()
        signals = detect_prompt_injection_signals(cleaned_text)

        chunk.section_path = new_section_path
        chunk.text = cleaned_text
        chunk.normalized_text = normalized
        chunk.content_hash = new_hash
        chunk.token_count = estimate_token_count(cleaned_text)
        chunk.language = detect_language(cleaned_text)
        chunk.has_injection_flag = bool(signals)
        chunk.tsv = func.to_tsvector("simple", normalized)
        chunk.metadata_json = {**(chunk.metadata_json or {}), "injection_signals": signals}
        await session.flush()

        # Re-embed ONLY this chunk
        target_model = model_gateway.resolve_embedding_model()
        target_dim = int(settings.EMBEDDING_DIM or 768)
        emb_res = await model_gateway.embed_texts(
            [f"{new_section_path}\n{cleaned_text}"],
            task_type="RETRIEVAL_DOCUMENT",
            tenant_id=str(t_id),
            model=target_model,
            dim=target_dim,
        )
        new_vec = emb_res.embeddings[0]

        existing_emb = (
            await session.execute(
                select(PolicyChunkEmbedding).where(
                    PolicyChunkEmbedding.tenant_id == t_id,
                    PolicyChunkEmbedding.chunk_id == chunk.id,
                    PolicyChunkEmbedding.model == target_model,
                    PolicyChunkEmbedding.dim == target_dim,
                )
            )
        ).scalar_one_or_none()

        if existing_emb is not None:
            existing_emb.content_hash = new_hash
            existing_emb.embedding = new_vec
        else:
            session.add(
                PolicyChunkEmbedding(
                    tenant_id=t_id,
                    chunk_id=chunk.id,
                    content_hash=new_hash,
                    model=target_model,
                    dim=target_dim,
                    embedding=new_vec,
                )
            )

        # Recalculate document-level prompt injection flag from all chunks & pages
        await cls._recalculate_injection_warning(session, tenant_id=t_id, doc=doc)

        if (
            payload.mark_reviewed
            and doc.ingestion_status == PolicyIngestionStatus.NEEDS_REVIEW.value
        ):
            unreviewed_pages = (
                await session.execute(
                    select(func.count(PolicyDocumentPage.id)).where(
                        PolicyDocumentPage.tenant_id == t_id,
                        PolicyDocumentPage.document_id == doc.id,
                        (PolicyDocumentPage.needs_ocr.is_(True))
                        | (PolicyDocumentPage.extraction_status != "ok"),
                    )
                )
            ).scalar() or 0
            if int(unreviewed_pages) == 0:
                doc.ingestion_status = assert_ingestion_transition(
                    doc.ingestion_status,
                    PolicyIngestionStatus.INDEXED.value,
                )
                doc.progress_stage = "indexed"

        await write_audit(
            session,
            action="policy_chunk.updated",
            target_type="policy_chunk",
            target_id=chunk.id,
            details={
                "policy_document_id": str(doc.id),
                "chunk_index": chunk.chunk_index,
                "content_hash": new_hash,
                "token_count": chunk.token_count,
                "has_injection_flag": chunk.has_injection_flag,
            },
            actor_id=current_user.user_id,
            tenant_id=t_id,
        )
        await session.flush()
        return PolicyChunkPreviewItem.model_validate(chunk)

    @classmethod
    async def fix_page(
        cls,
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
        policy_id: UUID,
        page_number: int,
        payload: PolicyPageFixRequest,
    ) -> PolicyDocumentResponse:
        """
        Repairs a low-confidence / `needs_ocr` page with human-verified cleaned text,
        removes that page from `doc.low_confidence_pages`, recalculates prompt-injection flags,
        re-chunks & idempotently re-embeds, and promotes `needs_review -> indexed` once zero
        unreviewed pages remain.
        """
        t_id = UUID(str(current_user.tenant_id))
        doc = await cls._get_tenant_document_or_404(session, tenant_id=t_id, policy_id=policy_id)

        if doc.lifecycle_status != PolicyLifecycleStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot fix pages while policy lifecycle_status is '{doc.lifecycle_status}'. "
                    "Only 'draft' policies may be edited."
                ),
            )

        page = (
            await session.execute(
                select(PolicyDocumentPage).where(
                    PolicyDocumentPage.tenant_id == t_id,
                    PolicyDocumentPage.document_id == doc.id,
                    PolicyDocumentPage.page_number == page_number,
                )
            )
        ).scalar_one_or_none()
        if page is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Page {page_number} not found on policy '{policy_id}'.",
            )

        cleaned = normalize_bilingual_text(payload.cleaned_text)
        if not cleaned:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Repaired page text cannot be empty.",
            )

        page_signals = detect_prompt_injection_signals(cleaned)
        page.cleaned_text = cleaned
        page.language = detect_language(cleaned)
        page.quality_score = 1.0
        page.needs_ocr = False
        page.extraction_status = "ok"
        page.error_message = None
        page.metadata_json = {
            **(page.metadata_json or {}),
            "manual_fix": True,
            "quality_reasons": [],
            "injection_signals": page_signals,
        }

        doc.low_confidence_pages = [
            item
            for item in (doc.low_confidence_pages or [])
            if item.get("page_number") != page_number
        ]
        await session.flush()

        # Re-chunk and idempotently re-embed
        await PolicyIngestionService.chunk_and_index_lexical(
            session,
            tenant_id=t_id,
            document_id=doc.id,
        )
        await PolicyIngestionService.embed_chunks_idempotent(
            session,
            tenant_id=t_id,
            document_id=doc.id,
        )

        # Check remaining unreviewed pages and recalculate prompt-injection warning
        remaining_pages = (
            (
                await session.execute(
                    select(PolicyDocumentPage).where(
                        PolicyDocumentPage.tenant_id == t_id,
                        PolicyDocumentPage.document_id == doc.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        unreviewed = [p for p in remaining_pages if p.needs_ocr or p.extraction_status != "ok"]
        if not unreviewed and doc.ingestion_status != PolicyIngestionStatus.INDEXED.value:
            doc.ingestion_status = assert_ingestion_transition(
                doc.ingestion_status,
                PolicyIngestionStatus.INDEXED.value,
            )
            doc.progress_pct = 100
            doc.progress_stage = "indexed"

        await cls._recalculate_injection_warning(session, tenant_id=t_id, doc=doc)

        await write_audit(
            session,
            action="policy_page.fixed",
            target_type="policy_document",
            target_id=doc.id,
            details={
                "page_number": page_number,
                "ingestion_status": doc.ingestion_status,
                "remaining_low_confidence": len(unreviewed),
            },
            actor_id=current_user.user_id,
            tenant_id=t_id,
        )
        await session.commit()
        await set_tenant_context(session, str(t_id), current_user.clerk_user_id)

        refreshed = await cls._get_tenant_document_or_404(
            session,
            tenant_id=t_id,
            policy_id=doc.id,
        )
        return PolicyDocumentResponse.model_validate(refreshed)

    @classmethod
    async def reingest_policy(
        cls,
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
        policy_id: UUID,
    ) -> PolicyDocumentResponse:
        """Re-queues and re-runs ingestion for a non-archived policy document."""
        t_id = UUID(str(current_user.tenant_id))
        doc = await cls._get_tenant_document_or_404(session, tenant_id=t_id, policy_id=policy_id)

        if doc.lifecycle_status in (
            PolicyLifecycleStatus.ARCHIVED.value,
            PolicyLifecycleStatus.SUPERSEDED.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot re-ingest policy with lifecycle_status '{doc.lifecycle_status}'.",
            )

        if doc.ingestion_status != PolicyIngestionStatus.QUEUED.value:
            try:
                doc.ingestion_status = assert_ingestion_transition(
                    doc.ingestion_status,
                    PolicyIngestionStatus.QUEUED.value,
                )
            except PolicyTransitionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc

        doc.progress_pct = 0
        doc.progress_stage = "queued"
        doc.error_message = None

        await write_audit(
            session,
            action="policy_document.reingest_requested",
            target_type="policy_document",
            target_id=doc.id,
            details={"title": doc.title, "version": doc.version},
            actor_id=current_user.user_id,
            tenant_id=t_id,
        )
        await session.commit()

        await set_tenant_context(session, str(t_id), current_user.clerk_user_id)
        await cls._trigger_ingestion(
            session,
            tenant_id=t_id,
            document_id=doc.id,
            actor_clerk_id=current_user.clerk_user_id,
        )

        await set_tenant_context(session, str(t_id), current_user.clerk_user_id)
        refreshed = await cls._get_tenant_document_or_404(
            session,
            tenant_id=t_id,
            policy_id=doc.id,
        )
        return PolicyDocumentResponse.model_validate(refreshed)

    @classmethod
    async def submit_for_publish(
        cls,
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
        policy_id: UUID,
        approver_role: str = "owner",
    ) -> PolicyPublishResponse:
        """
        Submits an `indexed` `draft` policy to Stage 2 `policy.publish` approval:
        - Guards via `assert_lifecycle_transition('draft', 'review', ingestion_status=...)`.
        - Atomically creates `Approval(approval_type='policy.publish')`, links `doc.approval_id`,
          and transitions `doc.lifecycle_status = 'review'`.
        """
        t_id = UUID(str(current_user.tenant_id))
        doc = await cls._get_tenant_document_or_404(session, tenant_id=t_id, policy_id=policy_id)

        try:
            next_lifecycle = assert_lifecycle_transition(
                doc.lifecycle_status,
                PolicyLifecycleStatus.REVIEW.value,
                ingestion_status=doc.ingestion_status,
            )
        except PolicyTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

        chunk_count = int(
            (
                await session.execute(
                    select(func.count(PolicyChunk.id)).where(
                        PolicyChunk.tenant_id == t_id,
                        PolicyChunk.document_id == doc.id,
                    )
                )
            ).scalar()
            or 0
        )

        approval = await create_approval(
            db=session,
            current_user=current_user,
            request=ApprovalCreateRequest(
                approval_type="policy.publish",
                approver_role=approver_role,  # type: ignore[arg-type]
                payload={
                    "document_id": str(doc.id),
                    "title": doc.title,
                    "version": doc.version,
                    "visibility": doc.visibility,
                    "doc_type": doc.doc_type,
                    "supersedes_document_id": (
                        str(doc.supersedes_document_id) if doc.supersedes_document_id else None
                    ),
                    "chunk_count": chunk_count,
                    "has_prompt_injection_warning": bool(doc.has_prompt_injection_warning),
                },
            ),
            commit=False,
        )

        doc.lifecycle_status = next_lifecycle
        doc.approval_id = approval.id

        await write_audit(
            session,
            action="policy_document.publish_requested",
            target_type="policy_document",
            target_id=doc.id,
            details={
                "title": doc.title,
                "version": doc.version,
                "approval_id": str(approval.id),
                "approver_role": approver_role,
                "chunk_count": chunk_count,
            },
            actor_id=current_user.user_id,
            tenant_id=t_id,
        )
        await session.commit()
        await set_tenant_context(session, str(t_id), current_user.clerk_user_id)

        refreshed = await cls._get_tenant_document_or_404(
            session,
            tenant_id=t_id,
            policy_id=doc.id,
        )
        return PolicyPublishResponse(
            document=PolicyDocumentResponse.model_validate(refreshed),
            approval_id=approval.id,
            approval_status=approval.status,
        )

    @classmethod
    async def archive_policy(
        cls,
        session: AsyncSession,
        *,
        current_user: AuthenticatedUserContext,
        policy_id: UUID,
    ) -> PolicyArchiveResponse:
        """
        Soft-deletes a policy document (`lifecycle_status = 'archived'`), preserving version &
        audit history while deleting its `policy_chunk_embeddings` so archived chunks are
        immediately excluded from semantic search.
        """
        t_id = UUID(str(current_user.tenant_id))
        doc = await cls._get_tenant_document_or_404(session, tenant_id=t_id, policy_id=policy_id)

        if doc.lifecycle_status == PolicyLifecycleStatus.ARCHIVED.value:
            return PolicyArchiveResponse(status="archived", id=str(policy_id))

        try:
            doc.lifecycle_status = assert_lifecycle_transition(
                doc.lifecycle_status,
                PolicyLifecycleStatus.ARCHIVED.value,
                ingestion_status=doc.ingestion_status,
            )
        except PolicyTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

        # Remove vector embeddings for archived policy so they cannot surface in search
        chunk_ids = (
            (
                await session.execute(
                    select(PolicyChunk.id).where(
                        PolicyChunk.tenant_id == t_id,
                        PolicyChunk.document_id == doc.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if chunk_ids:
            await session.execute(
                delete(PolicyChunkEmbedding).where(
                    PolicyChunkEmbedding.tenant_id == t_id,
                    PolicyChunkEmbedding.chunk_id.in_(chunk_ids),
                )
            )

        await write_audit(
            session,
            action="policy_document.archived",
            target_type="policy_document",
            target_id=doc.id,
            details={
                "title": doc.title,
                "version": doc.version,
                "lifecycle_status": doc.lifecycle_status,
            },
            actor_id=current_user.user_id,
            tenant_id=t_id,
        )
        await session.flush()
        return PolicyArchiveResponse(status="archived", id=str(policy_id))

    @staticmethod
    async def _recalculate_injection_warning(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        doc: PolicyDocument,
    ) -> None:
        """Recomputes `doc.has_prompt_injection_warning` from current chunks and pages."""
        flagged_chunks = (
            await session.execute(
                select(func.count(PolicyChunk.id)).where(
                    PolicyChunk.tenant_id == tenant_id,
                    PolicyChunk.document_id == doc.id,
                    PolicyChunk.has_injection_flag.is_(True),
                )
            )
        ).scalar() or 0
        if int(flagged_chunks) > 0:
            doc.has_prompt_injection_warning = True
            return

        pages = (
            (
                await session.execute(
                    select(PolicyDocumentPage).where(
                        PolicyDocumentPage.tenant_id == tenant_id,
                        PolicyDocumentPage.document_id == doc.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        doc.has_prompt_injection_warning = any(
            bool(detect_prompt_injection_signals(p.cleaned_text)) for p in pages
        )
