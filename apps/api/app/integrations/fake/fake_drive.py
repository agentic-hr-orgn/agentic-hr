"""
Fake Google Drive Service with in-memory storage and deterministic fault injection.
Enforces ADR 006: drive.file scope least privilege.
"""

import uuid
from datetime import UTC, datetime

from app.integrations.base import DriveFile, DriveFolder, DriveService
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)

ALLOWED_DRIVE_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
}

MAX_DRIVE_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit


class FakeDriveService(DriveService):
    """
    Fake Google Drive service storing files and folders in memory.
    Enforces ADR 006 & docs/oauth-scopes.md: drive.file only, no broad Drive access.
    """

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.metadata: dict[str, DriveFile] = {}
        self.folders: dict[str, DriveFolder] = {}
        self.simulate_401_on_next_call: bool = False
        self.simulate_429_on_next_call: bool = False
        self.simulate_503_on_next_call: bool = False

    def reset(self) -> None:
        """Resets in-memory storage and fault injection flags."""
        self.files.clear()
        self.metadata.clear()
        self.folders.clear()
        self.simulate_401_on_next_call = False
        self.simulate_429_on_next_call = False
        self.simulate_503_on_next_call = False

    def _check_fault_injection(self) -> None:
        if self.simulate_401_on_next_call:
            self.simulate_401_on_next_call = False
            raise ConnectorAuthExpiredError("Simulated Google Drive 401 Unauthorized")
        if self.simulate_429_on_next_call:
            self.simulate_429_on_next_call = False
            raise ConnectorRateLimitError(
                "Simulated Google Drive 429 Too Many Requests", retry_after=1
            )
        if self.simulate_503_on_next_call:
            self.simulate_503_on_next_call = False
            raise ConnectorServiceUnavailableError("Simulated Google Drive 503 Service Unavailable")

    async def ensure_tenant_folder(
        self,
        folder_name: str,
        parent_folder_id: str | None = None,
        access_token: str | None = None,
    ) -> str:
        """Ensures a dedicated tenant folder exists under drive.file; returns folder_id."""
        self._check_fault_injection()

        # Check existing folder
        for f in self.folders.values():
            if f.name == folder_name and f.parent_folder_id == parent_folder_id:
                return f.folder_id

        folder_id = f"fake_folder_{uuid.uuid4().hex[:12]}"
        folder = DriveFolder(
            folder_id=folder_id,
            name=folder_name,
            parent_folder_id=parent_folder_id,
            web_view_link=f"https://drive.google.com/drive/folders/{folder_id}?usp=fake",
        )
        self.folders[folder_id] = folder
        return folder_id

    async def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str,
        folder_id: str | None = None,
        access_token: str | None = None,
    ) -> DriveFile:
        """Upload a file (e.g., CV) created or opened by the app."""
        self._check_fault_injection()

        clean_mime = mime_type.strip().lower()
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

        file_id = f"fake_file_{uuid.uuid4().hex[:12]}"
        self.files[file_id] = content
        now = datetime.now(UTC)
        drive_file = DriveFile(
            file_id=file_id,
            name=name,
            mime_type=mime_type,
            size_bytes=len(content),
            web_view_link=f"https://drive.google.com/file/d/{file_id}/view?usp=fake",
            folder_id=folder_id,
            created_time=now,
            modified_time=now,
        )
        self.metadata[file_id] = drive_file
        return drive_file

    async def download_file(
        self,
        file_id: str,
        access_token: str | None = None,
    ) -> bytes:
        """Downloads the binary content of a file owned by the app."""
        self._check_fault_injection()

        if file_id not in self.files:
            raise FileNotFoundError(f"File with ID '{file_id}' not found in Drive.")
        return self.files[file_id]

    async def list_files(
        self,
        folder_id: str | None = None,
        page_size: int = 50,
        access_token: str | None = None,
    ) -> list[DriveFile]:
        """Lists files created/opened by the app, optionally filtered by folder_id."""
        self._check_fault_injection()

        results = list(self.metadata.values())
        if folder_id is not None:
            results = [f for f in results if f.folder_id == folder_id]
        return results[:page_size]
