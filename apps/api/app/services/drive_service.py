"""
Google Drive Orchestration Service for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and ADR 006 (drive.file scope least privilege).
Enforces IdempotencyGate, Read-Only enforcement, Redis rate limits, and audit logging.
"""

import logging
import uuid
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import IdempotencyGate, compute_payload_hash
from app.integrations.base import DriveFile
from app.integrations.factory import get_drive_service
from app.integrations.google.drive_client import (
    ALLOWED_DRIVE_MIME_TYPES,
    MAX_DRIVE_FILE_SIZE_BYTES,
)
from app.models.drive import DriveFileRecord
from app.services.connector_call_wrapper import execute_connector_call
from app.services.connector_service import ConnectorService

logger = logging.getLogger("agentic_hr.services.drive")


class DriveManagerService:
    """Orchestrates Google Drive operations under drive.file scope with full governance."""

    @staticmethod
    async def ensure_tenant_folder(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        folder_name: str = "Agentic HR Documents",
        parent_folder_id: str | None = None,
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> str:
        """Finds or creates a dedicated tenant root folder in Google Drive."""
        t_id = UUID(str(tenant_id))
        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        drive_svc = get_drive_service()

        folder_id = await execute_connector_call(
            session=session,
            tenant_id=t_id,
            credential=cred,
            operation="drive.ensure_folder",
            is_write=True,
            call_factory=lambda token: drive_svc.ensure_tenant_folder(
                folder_name=folder_name,
                parent_folder_id=parent_folder_id,
                access_token=token,
            ),
            actor_id=actor_id,
            rate_limit_scope="drive.file",
            max_requests_per_minute=30,
            audit_target_type="connector_credential",
            audit_target_id=cred.id,
            audit_details={"folder_name": folder_name, "parent_folder_id": parent_folder_id},
        )
        return folder_id

    @staticmethod
    async def upload_file(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        name: str,
        content: bytes,
        mime_type: str,
        folder_id: str | None = None,
        idempotency_key: str | None = None,
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> DriveFileRecord:
        """Uploads a file to Google Drive with MIME validation, size checking, and idempotency."""
        t_id = UUID(str(tenant_id))
        clean_mime = mime_type.strip().lower()

        # 1. Validation
        if clean_mime not in ALLOWED_DRIVE_MIME_TYPES:
            raise ValueError(
                f"MIME type '{mime_type}' is not permitted for Drive upload. "
                f"Allowed types: {sorted(ALLOWED_DRIVE_MIME_TYPES)}"
            )
        if len(content) > MAX_DRIVE_FILE_SIZE_BYTES:
            raise ValueError(
                f"File size ({len(content)} bytes) exceeds the maximum allowed limit of "
                f"{MAX_DRIVE_FILE_SIZE_BYTES} bytes (10MB)."
            )

        # 2. Retrieve active credential
        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        # 3. Enforce IdempotencyGate
        effective_key = idempotency_key or f"auto_drive_{uuid.uuid4()}"
        payload_hash = compute_payload_hash(
            {
                "name": name,
                "mime_type": clean_mime,
                "folder_id": folder_id,
                "size_bytes": len(content),
                "credential_id": str(cred.id),
            }
        )

        async with IdempotencyGate(
            session=session,
            tenant_id=t_id,
            operation="drive.upload",
            idempotency_key=effective_key,
            payload_hash=payload_hash,
        ) as gate:
            if gate.is_replay:
                cached_id = gate.response_body.get("id")
                if cached_id:
                    stmt = select(DriveFileRecord).where(
                        DriveFileRecord.tenant_id == t_id,
                        DriveFileRecord.id == UUID(cached_id),
                    )
                    res = await session.execute(stmt)
                    cached_record = res.scalars().first()
                    if cached_record:
                        return cached_record

            # 4. Route through execute_connector_call
            drive_svc = get_drive_service()
            drive_file: DriveFile = await execute_connector_call(
                session=session,
                tenant_id=t_id,
                credential=cred,
                operation="drive.upload",
                is_write=True,
                call_factory=lambda token: drive_svc.upload_file(
                    name=name,
                    content=content,
                    mime_type=clean_mime,
                    folder_id=folder_id,
                    access_token=token,
                ),
                actor_id=actor_id,
                rate_limit_scope="drive.file",
                max_requests_per_minute=30,
                audit_target_type="connector_credential",
                audit_target_id=cred.id,
                audit_details={"name": name, "mime_type": clean_mime, "size": len(content)},
            )

            # 5. Persist record in public.drive_files
            record = DriveFileRecord(
                tenant_id=t_id,
                credential_id=cred.id,
                drive_file_id=drive_file.file_id,
                folder_id=drive_file.folder_id or folder_id,
                name=drive_file.name,
                mime_type=drive_file.mime_type,
                size_bytes=drive_file.size_bytes,
                web_view_link=drive_file.web_view_link,
            )
            session.add(record)
            await session.flush()

            response_data = {
                "id": str(record.id),
                "drive_file_id": record.drive_file_id,
                "name": record.name,
                "mime_type": record.mime_type,
                "size_bytes": record.size_bytes,
                "web_view_link": record.web_view_link,
            }
            gate.set_result(200, response_data)
            return record

    @staticmethod
    async def download_file(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        file_id: str,
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> tuple[bytes, DriveFileRecord | None]:
        """Downloads file binary bytes from Google Drive. Read-only operation."""
        t_id = UUID(str(tenant_id))
        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        # Verify tenant owns file in database (PostgreSQL authoritative source of truth)
        stmt = select(DriveFileRecord).where(
            DriveFileRecord.tenant_id == t_id,
            (DriveFileRecord.drive_file_id == file_id)
            | (DriveFileRecord.id == UUID(file_id) if file_id.count("-") == 4 else False),
        )
        res = await session.execute(stmt)
        record = res.scalars().first()

        if not record:
            raise FileNotFoundError(f"Drive file '{file_id}' not found for tenant.")

        target_file_id = record.drive_file_id

        drive_svc = get_drive_service()
        content = await execute_connector_call(
            session=session,
            tenant_id=t_id,
            credential=cred,
            operation="drive.download",
            is_write=False,  # Allowed even if is_read_only=True
            call_factory=lambda token: drive_svc.download_file(
                file_id=target_file_id,
                access_token=token,
            ),
            actor_id=actor_id,
            rate_limit_scope="drive.file",
            max_requests_per_minute=30,
        )
        return content, record

    @staticmethod
    async def list_files(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        folder_id: str | None = None,
        page_size: int = 50,
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> list[DriveFileRecord]:
        """Lists files owned by the tenant. Read-only operation."""
        t_id = UUID(str(tenant_id))
        stmt = select(DriveFileRecord).where(DriveFileRecord.tenant_id == t_id)
        if folder_id:
            stmt = stmt.where(DriveFileRecord.folder_id == folder_id)
        stmt = stmt.order_by(desc(DriveFileRecord.created_at)).limit(page_size)

        res = await session.execute(stmt)
        return list(res.scalars().all())
