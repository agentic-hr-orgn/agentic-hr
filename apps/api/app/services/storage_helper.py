"""
Reusable Tenant-Scoped File Storage Helper for Agentic HR Core (Stage 4+).
Supports tenant-isolated local disk storage under `.local_storage/<tenant_id>/...`
and Google Drive storage via `DriveManagerService` under `drive.file` scope (ADR 006).
"""

import io
import logging
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.drive_service import DriveManagerService

logger = logging.getLogger("agentic_hr.services.storage")

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
UPLOAD_READ_CHUNK_BYTES = 64 * 1024  # 64 KB

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Repository root -> .local_storage/ (git-ignored)
_DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parents[4] / ".local_storage"

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class StoredFileRef:
    """Reference to a persisted file in local or Google Drive storage."""

    storage_provider: str
    storage_key: str
    size_bytes: int
    web_view_link: str | None = None


def get_local_storage_root() -> Path:
    """Returns the root directory for tenant-isolated local disk storage."""
    override = os.environ.get("AGENTIC_HR_LOCAL_STORAGE_ROOT")
    root = Path(override) if override else _DEFAULT_STORAGE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def sanitize_filename(file_name: str) -> str:
    """Strips path separators, traversal tokens, and unsafe characters from an uploaded filename."""
    base = Path(str(file_name or "file")).name.replace("\x00", "").strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip("._")
    if not cleaned:
        cleaned = "uploaded_file"
    return cleaned[:180]


async def read_upload_bytes_limited(
    file: UploadFile,
    max_bytes: int = MAX_UPLOAD_SIZE_BYTES,
    *,
    error_code: str = "FILE_TOO_LARGE",
) -> bytes:
    """
    Streams an `UploadFile` in 64KB chunks and aborts immediately if total bytes
    exceed `max_bytes`, preventing unbounded `await file.read()` memory exhaustion.
    """
    buf = bytearray()
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            mb_label = round(max_bytes / (1024 * 1024), 1)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{error_code}: Uploaded file exceeds maximum allowed limit of "
                    f"{max_bytes} bytes ({mb_label:g}MB)."
                ),
            )
    return bytes(buf)


def validate_file_magic_bytes(
    content: bytes,
    expected_mime: str,
    *,
    error_code: str = "INVALID_FILE_SIGNATURE",
) -> None:
    """
    Verifies that the binary magic bytes / container structure of `content` match
    `expected_mime`, blocking spoofed executables or invalid archives before storage or OCR/RAG.
    """
    if not content:
        return

    mime = (expected_mime or "").split(";")[0].strip().lower()
    head = content[:32].lstrip(b"\xef\xbb\xbf")

    if mime == "application/pdf":
        if not head.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{error_code}: Invalid PDF magic-byte signature (expected '%PDF-').",
            )
        return

    if mime == "image/png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{error_code}: Invalid PNG magic-byte signature.",
            )
        return

    if mime == "image/jpeg":
        if not content.startswith(b"\xff\xd8\xff"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{error_code}: Invalid JPEG magic-byte signature.",
            )
        return

    if mime in (DOCX_MIME, XLSX_MIME):
        required_prefix = "word/" if mime == DOCX_MIME else "xl/"
        fmt_label = "DOCX" if mime == DOCX_MIME else "XLSX"
        if not content.startswith(b"PK\x03\x04"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{error_code}: Invalid {fmt_label} OOXML signature (expected ZIP header).",
            )
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = set(zf.namelist())
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{error_code}: Corrupted {fmt_label} OOXML archive.",
            ) from exc

        if "[Content_Types].xml" not in names or not any(
            n.startswith(required_prefix) for n in names
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{error_code}: Archive is missing required {fmt_label} OOXML structure "
                    f"('[Content_Types].xml' and '{required_prefix}*')."
                ),
            )
        return

    if mime == "text/plain":
        if b"\x00" in content or content.startswith(
            (b"MZ", b"\x7fELF", b"%PDF-", b"PK\x03\x04", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{error_code}: Binary content is not allowed for 'text/plain' uploads.",
            )
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{error_code}: Plain-text uploads must be valid UTF-8.",
            ) from exc
        return


class StorageHelper:
    """
    Tenant-isolated storage abstraction for Policy Documents (Prompt 2)
    and Employee Documents (Prompt 4).
    """

    @staticmethod
    def _resolve_local_path(tenant_id: UUID | str, storage_key: str) -> Path:
        t_str = str(UUID(str(tenant_id)))
        normalized_key = storage_key.replace("\\", "/").lstrip("/")
        if not normalized_key.startswith(f"{t_str}/"):
            raise PermissionError(
                f"Storage key '{storage_key}' does not belong to tenant '{t_str}'."
            )
        root = get_local_storage_root()
        tenant_root = (root / t_str).resolve()
        candidate = (root / normalized_key).resolve()
        try:
            candidate.relative_to(tenant_root)
        except ValueError as exc:
            raise PermissionError(
                f"Path traversal blocked for storage key '{storage_key}'."
            ) from exc
        return candidate

    @classmethod
    async def store_file(
        cls,
        *,
        tenant_id: UUID | str,
        category: str,
        file_name: str,
        content: bytes,
        mime_type: str,
        storage_provider: str = "local",
        session: AsyncSession | None = None,
        actor_id: UUID | str | None = None,
        drive_folder_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> StoredFileRef:
        """
        Persists bytes to either tenant-isolated local disk storage or Google Drive (`drive.file`).
        """
        if not content:
            raise ValueError("Uploaded file content cannot be empty.")
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise ValueError(
                f"File size ({len(content)} bytes) exceeds maximum limit of "
                f"{MAX_UPLOAD_SIZE_BYTES} bytes (10MB)."
            )

        provider = (storage_provider or "local").strip().lower()
        t_str = str(UUID(str(tenant_id)))
        safe_category = _SAFE_FILENAME_RE.sub("_", category.strip().lower()).strip("_") or "files"
        safe_name = sanitize_filename(file_name)

        if provider == "local":
            file_uuid = uuid.uuid4().hex
            rel_key = f"{t_str}/{safe_category}/{file_uuid}_{safe_name}"
            target_path = cls._resolve_local_path(t_str, rel_key)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(content)
            return StoredFileRef(
                storage_provider="local",
                storage_key=rel_key,
                size_bytes=len(content),
                web_view_link=None,
            )

        if provider == "google_drive":
            if session is None:
                raise ValueError(
                    "Database session is required when storing files with storage_provider='google_drive'."
                )
            record = await DriveManagerService.upload_file(
                session=session,
                tenant_id=t_str,
                name=safe_name,
                content=content,
                mime_type=mime_type,
                folder_id=drive_folder_id,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
            )
            return StoredFileRef(
                storage_provider="google_drive",
                storage_key=record.drive_file_id,
                size_bytes=record.size_bytes,
                web_view_link=record.web_view_link,
            )

        raise ValueError(
            f"Unsupported storage_provider '{storage_provider}'. Expected 'local' or 'google_drive'."
        )

    @classmethod
    async def read_file(
        cls,
        *,
        tenant_id: UUID | str,
        storage_provider: str,
        storage_key: str,
        session: AsyncSession | None = None,
        actor_id: UUID | str | None = None,
    ) -> bytes:
        """Reads file bytes enforcing strict tenant ownership."""
        provider = storage_provider.strip().lower()
        if provider == "local":
            target_path = cls._resolve_local_path(tenant_id, storage_key)
            if not target_path.exists():
                raise FileNotFoundError(f"Stored file '{storage_key}' not found.")
            return target_path.read_bytes()

        if provider == "google_drive":
            if session is None:
                raise ValueError(
                    "Database session is required when reading files from 'google_drive'."
                )
            content, _ = await DriveManagerService.download_file(
                session=session,
                tenant_id=tenant_id,
                file_id=storage_key,
                actor_id=actor_id,
            )
            return content

        raise ValueError(f"Unsupported storage_provider '{storage_provider}'.")

    @classmethod
    def delete_local_file(cls, *, tenant_id: UUID | str, storage_key: str) -> bool:
        """Best-effort deletion of a tenant-owned local file."""
        try:
            target_path = cls._resolve_local_path(tenant_id, storage_key)
            if target_path.exists():
                target_path.unlink()
                return True
        except Exception as exc:
            logger.warning(f"Could not delete local file '{storage_key}': {exc}")
        return False
