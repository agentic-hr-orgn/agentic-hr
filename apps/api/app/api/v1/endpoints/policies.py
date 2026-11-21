"""
Policy Document Lifecycle, Preview, Edit & Approval REST API Endpoints (Stage 5 Prompt 4).

Endpoints:
- GET  /api/v1/policies: Lists policy documents with RBAC visibility & filters.
- GET  /api/v1/policies/review-queue: Lists needs_review / failed documents (Owner / HR Head).
- POST /api/v1/policies (+ /upload): Uploads PDF/DOCX/TXT (<= 10MB) with versioning & ingestion.
- GET  /api/v1/policies/{policy_id}/preview: Returns document metadata, pages, and chunks.
- PATCH /api/v1/policies/{policy_id}/chunks/{chunk_id}: Edits draft chunk & re-embeds.
- POST /api/v1/policies/{policy_id}/pages/{page_number}/fix: Repairs low-confidence page text.
- POST /api/v1/policies/{policy_id}/reingest: Rate-limited re-ingestion trigger.
- POST /api/v1/policies/{policy_id}/publish: Submits indexed draft to Stage 2 `policy.publish`.
- DELETE /api/v1/policies/{policy_id}: Soft-deletes (`lifecycle_status='archived'`).
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
)
from app.db.session import get_db
from app.schemas.policy import (
    PolicyArchiveResponse,
    PolicyChunkPreviewItem,
    PolicyChunkUpdateRequest,
    PolicyDocumentResponse,
    PolicyPageFixRequest,
    PolicyPreviewResponse,
    PolicyPublishRequest,
    PolicyPublishResponse,
    PolicyReviewQueueItem,
)
from app.schemas.policy_search import PolicySearchRequest, PolicySearchResponse
from app.services.policy_search_service import search_policy
from app.services.policy_service import PolicyService
from app.services.storage_helper import MAX_UPLOAD_SIZE_BYTES, read_upload_bytes_limited

router = APIRouter()


@router.get("", response_model=list[PolicyDocumentResponse])
async def list_policy_documents(
    doc_type: str | None = Query(None),
    visibility: str | None = Query(None),
    lifecycle_status: str | None = Query(None),
    ingestion_status: str | None = Query(None),
    language: str | None = Query(None),
    needs_review_only: bool = Query(False),
    include_archived: bool = Query(False),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PolicyDocumentResponse]:
    """Lists tenant policy documents filtered by metadata and RBAC visibility."""
    return await PolicyService.list_policies(
        db,
        current_user=current_user,
        doc_type=doc_type,
        visibility=visibility,
        lifecycle_status=lifecycle_status,
        ingestion_status=ingestion_status,
        language=language,
        needs_review_only=needs_review_only,
        include_archived=include_archived,
    )


@router.get("/review-queue", response_model=list[PolicyReviewQueueItem])
async def get_policy_review_queue(
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> list[PolicyReviewQueueItem]:
    """Lists policy documents requiring OCR review or ingestion repair (Owner / HR Head only)."""
    return await PolicyService.list_review_queue(
        db,
        current_user=current_user,
    )


@router.post(
    "/search",
    response_model=PolicySearchResponse,
    dependencies=[Depends(RateLimiter(requests=60, window_seconds=60, scope="policies.search"))],
)
async def search_policy_documents(
    payload: PolicySearchRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PolicySearchResponse:
    """
    Executes hybrid bilingual retrieval (SQL pre-filter + pgvector HNSW + simple FTS + pg_trgm
    + RRF + reranker) strictly within the caller's tenant and role visibility boundary.
    """
    return await search_policy(db, current_user, payload)


@router.post(
    "",
    response_model=PolicyDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RateLimiter(requests=60, window_seconds=60, scope="policies.upload"))],
)
@router.post(
    "/upload",
    response_model=PolicyDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RateLimiter(requests=60, window_seconds=60, scope="policies.upload"))],
)
async def upload_policy_document(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    doc_type: str = Form("company_policy"),
    visibility: str = Form("all"),
    language: str | None = Form(None),
    effective_date: date | None = Form(None),
    source: str = Form("upload"),
    storage_provider: str = Form("local"),
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> PolicyDocumentResponse:
    """
    Accepts PDF/DOCX/TXT policy files up to 10MB via streaming cutoff + magic-byte validation,
    creates a new versioned `PolicyDocument`, emits `policy.uploaded`, and triggers ingestion.
    """
    content = await read_upload_bytes_limited(
        file,
        MAX_UPLOAD_SIZE_BYTES,
        error_code="POLICY_FILE_TOO_LARGE",
    )
    return await PolicyService.upload_policy(
        db,
        current_user=current_user,
        file_name=file.filename or "policy.pdf",
        content=content,
        content_type=file.content_type,
        title=title,
        doc_type=doc_type,
        visibility=visibility,
        language=language,
        effective_date=effective_date,
        source=source,
        storage_provider=storage_provider,
    )


@router.get("/{policy_id}/preview", response_model=PolicyPreviewResponse)
async def preview_policy_document(
    policy_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PolicyPreviewResponse:
    """Returns policy metadata, extracted pages, and indexed chunks."""
    return await PolicyService.get_preview(
        db,
        current_user=current_user,
        policy_id=policy_id,
    )


@router.patch(
    "/{policy_id}/chunks/{chunk_id}",
    response_model=PolicyChunkPreviewItem,
)
async def update_policy_chunk(
    policy_id: UUID,
    chunk_id: UUID,
    payload: PolicyChunkUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> PolicyChunkPreviewItem:
    """Edits a draft policy chunk, re-normalizes, and re-embeds ONLY that chunk."""
    result = await PolicyService.update_chunk(
        db,
        current_user=current_user,
        policy_id=policy_id,
        chunk_id=chunk_id,
        payload=payload,
    )
    await db.commit()
    return result


@router.post(
    "/{policy_id}/pages/{page_number}/fix",
    response_model=PolicyDocumentResponse,
)
async def fix_policy_page(
    policy_id: UUID,
    page_number: int,
    payload: PolicyPageFixRequest,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> PolicyDocumentResponse:
    """Saves manually corrected text for a `needs_ocr` page and re-indexes the document."""
    return await PolicyService.fix_page(
        db,
        current_user=current_user,
        policy_id=policy_id,
        page_number=page_number,
        payload=payload,
    )


@router.post(
    "/{policy_id}/reingest",
    response_model=PolicyDocumentResponse,
    dependencies=[Depends(RateLimiter(requests=20, window_seconds=60, scope="policies.reingest"))],
)
async def reingest_policy_document(
    policy_id: UUID,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> PolicyDocumentResponse:
    """Re-queues and runs ingestion for a non-terminal policy document."""
    return await PolicyService.reingest_policy(
        db,
        current_user=current_user,
        policy_id=policy_id,
    )


@router.post("/{policy_id}/publish", response_model=PolicyPublishResponse)
async def publish_policy_document(
    policy_id: UUID,
    payload: PolicyPublishRequest | None = None,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> PolicyPublishResponse:
    """Submits an indexed `draft` policy for Stage 2 `policy.publish` approval."""
    approver_role = payload.approver_role if payload else "owner"
    return await PolicyService.submit_for_publish(
        db,
        current_user=current_user,
        policy_id=policy_id,
        approver_role=approver_role,
    )


@router.delete("/{policy_id}", response_model=PolicyArchiveResponse)
async def delete_policy_document(
    policy_id: UUID,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> PolicyArchiveResponse:
    """Soft-deletes (`lifecycle_status='archived'`) a policy document for the active tenant."""
    result = await PolicyService.archive_policy(
        db,
        current_user=current_user,
        policy_id=policy_id,
    )
    await db.commit()
    return result
