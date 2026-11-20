"""
Per-Employee Document Vault Service for Agentic HR Core (Stage 4 Prompt 4).
Supports CNIC scans, employment contracts, certificates, and other employee files.

Storage routing:
- Uses `StorageHelper` with `google_drive` (`drive.file` scope) when the tenant has
  an active, connected, non-read-only Google connector with `drive.file` scope.
- Falls back cleanly to tenant-scoped local storage when Drive is disconnected,
  read-only, or returns a connector error (committing any 401 circuit-breaker trip first
  and re-establishing transaction-local RLS context before DB writes).
"""

import logging
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUserContext
from app.models.connector import ConnectorCredential
from app.models.employee import Employee
from app.models.employee_document import EmployeeDocument
from app.models.tenant import set_tenant_context
from app.schemas.employee import EmployeeDocumentResponse
from app.services.audit_service import write_audit
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorError,
    CredentialNotFoundError,
    ReadOnlyConnectorError,
)
from app.services.storage_helper import MAX_UPLOAD_SIZE_BYTES, StorageHelper

logger = logging.getLogger("agentic_hr.services.employee_document")

ALLOWED_EMPLOYEE_DOC_TYPES: set[str] = {"cnic", "contract", "certificate", "other"}

ALLOWED_EMPLOYEE_DOC_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

ALLOWED_EMPLOYEE_DOC_MIME_TYPES: set[str] = set(ALLOWED_EMPLOYEE_DOC_EXTENSIONS.values())

DRIVE_FILE_SCOPES = {
    "drive.file",
    "https://www.googleapis.com/auth/drive.file",
}


class EmployeeDocumentService:
    """Manages per-employee documents with strict tenant + employee ownership and RBAC."""

    @staticmethod
    def _can_read_employee_documents(
        actor: AuthenticatedUserContext,
        employee_user_id: UUID | None,
    ) -> bool:
        """
        Defense-in-depth RBAC: owner, manager, hr_head, payroll_admin, or the
        employee themselves may view/download an employee's documents.
        Unrelated peer employees are blocked from accessing other employees' CNICs/contracts.
        """
        if actor.role in ("owner", "manager") or actor.is_hr_head or actor.is_payroll_admin:
            return True
        return bool(employee_user_id is not None and str(employee_user_id) == str(actor.user_id))

    @staticmethod
    def _validate_and_resolve_mime(file_name: str, content_type: str | None) -> str:
        ext = Path(file_name or "").suffix.lower()
        clean_mime = (content_type or "").split(";")[0].strip().lower()

        if clean_mime in ALLOWED_EMPLOYEE_DOC_MIME_TYPES:
            if ext and ext not in ALLOWED_EMPLOYEE_DOC_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"INVALID_DOCUMENT_FILE_TYPE: Extension '{ext}' is not supported. "
                        "Allowed formats: PDF, PNG, JPEG, DOCX."
                    ),
                )
            return clean_mime

        if ext in ALLOWED_EMPLOYEE_DOC_EXTENSIONS and clean_mime in (
            "",
            "application/octet-stream",
        ):
            return ALLOWED_EMPLOYEE_DOC_EXTENSIONS[ext]

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"INVALID_DOCUMENT_FILE_TYPE: File '{file_name}' ({content_type}) is not supported. "
                "Allowed formats: PDF, PNG, JPEG, DOCX."
            ),
        )

    @staticmethod
    async def _has_writable_google_drive(session: AsyncSession, tenant_id: UUID) -> bool:
        """Returns True if the tenant has an active, connected, writable Google Drive connector."""
        stmt = (
            select(ConnectorCredential)
            .where(
                ConnectorCredential.tenant_id == tenant_id,
                ConnectorCredential.provider == "google",
                ConnectorCredential.status == "connected",
            )
            .order_by(desc(ConnectorCredential.created_at))
        )
        cred = (await session.execute(stmt)).scalars().first()
        if not cred or cred.is_read_only:
            return False
        scopes = set(cred.scopes or [])
        return bool(scopes & DRIVE_FILE_SCOPES)

    @staticmethod
    async def _get_employee_or_404(
        session: AsyncSession,
        tenant_id: UUID,
        employee_id: UUID,
    ) -> Employee:
        stmt = select(Employee).where(
            Employee.tenant_id == tenant_id,
            Employee.id == employee_id,
        )
        emp = (await session.execute(stmt)).scalar_one_or_none()
        if not emp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee '{employee_id}' not found.",
            )
        return emp

    @classmethod
    async def upload_document(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        employee_id: UUID,
        document_type: str,
        title: str | None,
        expiry_date: date | None,
        file_bytes: bytes,
        filename: str,
        mime_type: str | None,
        actor: AuthenticatedUserContext,
    ) -> EmployeeDocumentResponse:
        """
        Validates MIME type & size (<=10MB), stores via Google Drive when connected/writable
        (or falls back cleanly to tenant-scoped local storage), persists `employee_documents`,
        and writes immutable `audit_logs`.
        """
        if not (actor.role in ("owner", "manager") or actor.is_hr_head):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Owner, Manager, or HR Head privileges required to upload documents.",
            )

        t_id = UUID(str(tenant_id))
        await cls._get_employee_or_404(session, t_id, employee_id)

        clean_doc_type = (document_type or "").strip().lower()
        if clean_doc_type not in ALLOWED_EMPLOYEE_DOC_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"INVALID_DOCUMENT_TYPE: '{document_type}' is not valid. "
                    f"Allowed types: {sorted(ALLOWED_EMPLOYEE_DOC_TYPES)}."
                ),
            )

        clean_file_name = (filename or "").strip()
        if not clean_file_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file must have a valid filename.",
            )

        resolved_mime = cls._validate_and_resolve_mime(clean_file_name, mime_type)

        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded document file cannot be empty.",
            )
        if len(file_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"DOCUMENT_FILE_TOO_LARGE: File size ({len(file_bytes)} bytes) exceeds "
                    f"maximum allowed limit of {MAX_UPLOAD_SIZE_BYTES} bytes (10MB)."
                ),
            )

        category = f"employee_docs_{employee_id}"
        stored = None

        # Execute external Drive upload BEFORE inserting the employee_documents DB row.
        if await cls._has_writable_google_drive(session, t_id):
            try:
                stored = await StorageHelper.store_file(
                    tenant_id=t_id,
                    category=category,
                    file_name=clean_file_name,
                    content=file_bytes,
                    mime_type=resolved_mime,
                    storage_provider="google_drive",
                    session=session,
                    actor_id=actor.user_id,
                )
            except ConnectorAuthExpiredError:
                # Durable 401 circuit-breaker: commit the tripped credential status
                # and re-establish transaction-local RLS context before falling back to local.
                logger.warning(
                    "Google Drive 401 during employee document upload for tenant %s; "
                    "committing circuit-breaker trip and falling back to local storage.",
                    t_id,
                )
                await session.commit()
                await set_tenant_context(session, str(t_id), actor.clerk_user_id)
            except (ReadOnlyConnectorError, CredentialNotFoundError, ConnectorError) as exc:
                logger.info(
                    "Google Drive unavailable (%s) for tenant %s; falling back to local storage.",
                    type(exc).__name__,
                    t_id,
                )

        if stored is None:
            stored = await StorageHelper.store_file(
                tenant_id=t_id,
                category=category,
                file_name=clean_file_name,
                content=file_bytes,
                mime_type=resolved_mime,
                storage_provider="local",
                session=session,
                actor_id=actor.user_id,
            )

        resolved_title = (title or "").strip() or Path(clean_file_name).stem.replace(
            "_", " "
        ).strip()

        doc = EmployeeDocument(
            tenant_id=t_id,
            employee_id=employee_id,
            document_type=clean_doc_type,
            title=resolved_title[:255],
            file_name=clean_file_name[:255],
            mime_type=resolved_mime,
            size_bytes=stored.size_bytes,
            storage_provider=stored.storage_provider,
            storage_key=stored.storage_key,
            web_view_link=stored.web_view_link,
            expiry_date=expiry_date,
            uploaded_by=UUID(str(actor.user_id)),
        )
        session.add(doc)
        await session.flush()

        await write_audit(
            session,
            action="employee_document.uploaded",
            target_type="employee_document",
            target_id=doc.id,
            details={
                "employee_id": str(employee_id),
                "document_type": doc.document_type,
                "title": doc.title,
                "file_name": doc.file_name,
                "mime_type": doc.mime_type,
                "size_bytes": doc.size_bytes,
                "storage_provider": doc.storage_provider,
                "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
            },
            actor_id=actor.user_id,
            tenant_id=t_id,
        )

        return EmployeeDocumentResponse.model_validate(doc)

    @classmethod
    async def list_documents(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        employee_id: UUID,
        actor: AuthenticatedUserContext,
    ) -> list[EmployeeDocumentResponse]:
        """Lists all documents for an employee after verifying tenant ownership and RBAC."""
        t_id = UUID(str(tenant_id))
        emp = await cls._get_employee_or_404(session, t_id, employee_id)

        if not cls._can_read_employee_documents(actor, emp.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You are not permitted to view this employee's documents.",
            )

        stmt = (
            select(EmployeeDocument)
            .where(
                EmployeeDocument.tenant_id == t_id,
                EmployeeDocument.employee_id == employee_id,
            )
            .order_by(desc(EmployeeDocument.created_at))
        )
        docs = (await session.execute(stmt)).scalars().all()
        return [EmployeeDocumentResponse.model_validate(d) for d in docs]

    @classmethod
    async def download_document(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        employee_id: UUID,
        document_id: UUID,
        actor: AuthenticatedUserContext,
    ) -> tuple[bytes, str, str]:
        """
        Verifies tenant + employee ownership and actor RBAC, retrieves exact original bytes
        from either `local` or `google_drive` via `StorageHelper.read_file`, and writes audit_logs.
        """
        t_id = UUID(str(tenant_id))
        emp = await cls._get_employee_or_404(session, t_id, employee_id)

        if not cls._can_read_employee_documents(actor, emp.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You are not permitted to download this employee's documents.",
            )

        stmt = select(EmployeeDocument).where(
            EmployeeDocument.tenant_id == t_id,
            EmployeeDocument.employee_id == employee_id,
            EmployeeDocument.id == document_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document '{document_id}' not found for employee '{employee_id}'.",
            )

        try:
            content = await StorageHelper.read_file(
                tenant_id=t_id,
                storage_provider=doc.storage_provider,
                storage_key=doc.storage_key,
                session=session,
                actor_id=actor.user_id,
            )
        except ConnectorAuthExpiredError as exc:
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="CONNECTOR_AUTH_EXPIRED: Google Drive credential expired; reconnect required.",
            ) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Stored file content for document '{document_id}' could not be found.",
            ) from exc

        await write_audit(
            session,
            action="employee_document.downloaded",
            target_type="employee_document",
            target_id=doc.id,
            details={
                "employee_id": str(employee_id),
                "document_type": doc.document_type,
                "file_name": doc.file_name,
                "storage_provider": doc.storage_provider,
            },
            actor_id=actor.user_id,
            tenant_id=t_id,
        )

        return content, doc.file_name, doc.mime_type

    @classmethod
    async def delete_document(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        employee_id: UUID,
        document_id: UUID,
        actor: AuthenticatedUserContext,
    ) -> dict[str, str]:
        """Deletes a per-employee document. Restricted strictly to `owner` and `is_hr_head`."""
        if not (actor.role == "owner" or actor.is_hr_head):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Only Owner or HR Head may delete employee documents.",
            )

        t_id = UUID(str(tenant_id))
        await cls._get_employee_or_404(session, t_id, employee_id)

        stmt = select(EmployeeDocument).where(
            EmployeeDocument.tenant_id == t_id,
            EmployeeDocument.employee_id == employee_id,
            EmployeeDocument.id == document_id,
        )
        doc = (await session.execute(stmt)).scalar_one_or_none()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document '{document_id}' not found for employee '{employee_id}'.",
            )

        storage_provider = doc.storage_provider
        storage_key = doc.storage_key
        doc_title = doc.title

        await session.delete(doc)
        await session.flush()

        await write_audit(
            session,
            action="employee_document.deleted",
            target_type="employee_document",
            target_id=document_id,
            details={
                "employee_id": str(employee_id),
                "title": doc_title,
                "storage_provider": storage_provider,
            },
            actor_id=actor.user_id,
            tenant_id=t_id,
        )

        if storage_provider == "local":
            StorageHelper.delete_local_file(tenant_id=t_id, storage_key=storage_key)

        return {"status": "deleted", "id": str(document_id)}
