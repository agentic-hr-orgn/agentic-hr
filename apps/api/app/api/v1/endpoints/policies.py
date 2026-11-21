"""
Policy Document Upload Stub REST API Endpoints for Agentic HR Core (Stage 4 Prompt 2).

Endpoints:
- GET /api/v1/policies: Lists tenant policy documents.
- POST /api/v1/policies: Uploads PDF/DOCX/TXT (<= 10MB) via multipart/form-data.
- DELETE /api/v1/policies/{policy_id}: Deletes a tenant policy document.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_owner_manager_or_hr_head,
)
from app.db.session import get_db
from app.schemas.policy import PolicyDocumentResponse
from app.services.policy_stub_service import PolicyStubService
from app.services.storage_helper import MAX_UPLOAD_SIZE_BYTES, read_upload_bytes_limited

router = APIRouter()


@router.get("", response_model=list[PolicyDocumentResponse])
async def list_policy_documents(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PolicyDocumentResponse]:
    """Lists all uploaded policy documents for the active tenant."""
    return await PolicyStubService.list_policies(
        session=db,
        tenant_id=current_user.tenant_id,
    )


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
    storage_provider: str = Form("local"),
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> PolicyDocumentResponse:
    """
    Accepts PDF/DOCX/TXT policy files up to 10MB via multipart/form-data, stores via
    tenant-scoped StorageHelper, persists `policy_documents` with `ingestion_status='queued_stub'`,
    and emits transactional outbox event `policy.uploaded.stub`.
    """
    content = await read_upload_bytes_limited(
        file,
        MAX_UPLOAD_SIZE_BYTES,
        error_code="POLICY_FILE_TOO_LARGE",
    )
    result = await PolicyStubService.upload_policy_stub(
        session=db,
        tenant_id=current_user.tenant_id,
        file_name=file.filename or "policy.pdf",
        content=content,
        content_type=file.content_type,
        title=title,
        storage_provider=storage_provider,
        actor_id=current_user.user_id,
    )
    await db.commit()
    return result


@router.delete("/{policy_id}")
async def delete_policy_document(
    policy_id: UUID,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Deletes a policy document for the active tenant."""
    result = await PolicyStubService.delete_policy(
        session=db,
        tenant_id=current_user.tenant_id,
        policy_id=policy_id,
        actor_id=current_user.user_id,
    )
    await db.commit()
    return result
