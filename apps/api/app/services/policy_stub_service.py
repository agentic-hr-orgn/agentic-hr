"""
Policy Upload Stub Service for Agentic HR Core (Stage 4 Prompt 2 — Onboarding Step 2).
Accepts PDF/DOCX/TXT up to 10MB, stores files via tenant-scoped StorageHelper,
persists `policy_documents` with `ingestion_status="queued_stub"`, and emits
transactional outbox event `policy.uploaded.stub` (ready for Stage 5 ingestion).
"""

from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.policy_document import PolicyDocument
from app.schemas.policy import PolicyDocumentResponse
from app.services.audit_service import write_audit
from app.services.outbox_service import enqueue_outbox_event
from app.services.storage_helper import MAX_UPLOAD_SIZE_BYTES, StorageHelper

ALLOWED_POLICY_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}

ALLOWED_POLICY_MIME_TYPES: set[str] = set(ALLOWED_POLICY_EXTENSIONS.values())


class PolicyStubService:
    """Manages Step 2 corporate policy uploads and stubbed ingestion outbox events."""

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
    async def list_policies(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
    ) -> list[PolicyDocumentResponse]:
        """Lists all uploaded policy documents for the active tenant."""
        t_id = UUID(str(tenant_id))
        stmt = (
            select(PolicyDocument)
            .where(PolicyDocument.tenant_id == t_id)
            .order_by(desc(PolicyDocument.created_at))
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [PolicyDocumentResponse.model_validate(r) for r in rows]

    @classmethod
    async def upload_policy_stub(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        file_name: str,
        content: bytes,
        content_type: str | None,
        title: str | None = None,
        storage_provider: str = "local",
        actor_id: UUID | str | None = None,
    ) -> PolicyDocumentResponse:
        """
        Validates and stores a policy file (PDF/DOCX/TXT <= 10MB), creates a PolicyDocument
        with ingestion_status='queued_stub', and enqueues outbox event 'policy.uploaded.stub'.
        """
        t_id = UUID(str(tenant_id))
        clean_file_name = (file_name or "").strip()
        if not clean_file_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file must have a valid filename.",
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

        try:
            stored = await StorageHelper.store_file(
                tenant_id=t_id,
                category="policies",
                file_name=clean_file_name,
                content=content,
                mime_type=resolved_mime,
                storage_provider=storage_provider,
                session=session,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        resolved_title = (title or "").strip() or Path(clean_file_name).stem.replace(
            "_", " "
        ).strip()
        actor_uuid = UUID(str(actor_id)) if actor_id else None

        doc = PolicyDocument(
            tenant_id=t_id,
            title=resolved_title[:255],
            file_name=clean_file_name[:255],
            mime_type=resolved_mime,
            size_bytes=stored.size_bytes,
            storage_provider=stored.storage_provider,
            storage_key=stored.storage_key,
            ingestion_status="queued_stub",
            uploaded_by=actor_uuid,
        )
        session.add(doc)
        await session.flush()

        await enqueue_outbox_event(
            session=session,
            tenant_id=t_id,
            event_type="policy.uploaded.stub",
            event_payload={
                "policy_document_id": str(doc.id),
                "title": doc.title,
                "file_name": doc.file_name,
                "mime_type": doc.mime_type,
                "size_bytes": doc.size_bytes,
                "storage_provider": doc.storage_provider,
                "storage_key": doc.storage_key,
                "ingestion_status": doc.ingestion_status,
            },
        )

        await write_audit(
            session,
            action="policy_document.uploaded_stub",
            target_type="policy_document",
            target_id=doc.id,
            details={
                "title": doc.title,
                "file_name": doc.file_name,
                "mime_type": doc.mime_type,
                "size_bytes": doc.size_bytes,
                "storage_provider": doc.storage_provider,
                "ingestion_status": doc.ingestion_status,
            },
            actor_id=actor_id,
            tenant_id=t_id,
        )

        return PolicyDocumentResponse.model_validate(doc)

    @staticmethod
    async def delete_policy(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        policy_id: UUID,
        actor_id: UUID | str | None = None,
    ) -> dict[str, str]:
        """Deletes a tenant-owned policy document record and cleans up local storage if applicable."""
        t_id = UUID(str(tenant_id))
        stmt = select(PolicyDocument).where(
            PolicyDocument.tenant_id == t_id,
            PolicyDocument.id == policy_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Policy document '{policy_id}' not found.",
            )

        storage_provider = doc.storage_provider
        storage_key = doc.storage_key
        title = doc.title

        await session.delete(doc)
        await session.flush()

        await write_audit(
            session,
            action="policy_document.deleted",
            target_type="policy_document",
            target_id=policy_id,
            details={"title": title, "storage_provider": storage_provider},
            actor_id=actor_id,
            tenant_id=t_id,
        )

        if storage_provider == "local":
            StorageHelper.delete_local_file(tenant_id=t_id, storage_key=storage_key)

        return {"status": "deleted", "id": str(policy_id)}
