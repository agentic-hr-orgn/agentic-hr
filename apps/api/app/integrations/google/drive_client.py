"""
Google Drive Service Client (Real HTTP/API Implementation).
Scope: https://www.googleapis.com/auth/drive.file ONLY per ADR 006.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.integrations.base import DriveFile, DriveService
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)

logger = logging.getLogger("agentic_hr.integrations.google.drive")

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


class GoogleDriveService(DriveService):
    """
    Real Google Drive service client via Google Drive API v3.
    Scope: https://www.googleapis.com/auth/drive.file ONLY per ADR 006.
    """

    ALLOWED_SCOPE = "https://www.googleapis.com/auth/drive.file"

    def __init__(
        self,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    def _validate_file(self, content: bytes, mime_type: str) -> None:
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

    def _handle_error_response(self, resp: httpx.Response, action: str) -> None:
        if resp.status_code == 401 or "invalid_grant" in resp.text:
            raise ConnectorAuthExpiredError(
                f"Google Drive API 401 Unauthorized during {action}: {resp.text}"
            )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 1))
            raise ConnectorRateLimitError(
                f"Google Drive API 429 Rate Limited during {action}: {resp.text}",
                retry_after=retry_after,
            )
        if resp.status_code in (500, 502, 503, 504):
            raise ConnectorServiceUnavailableError(
                f"Google Drive API {resp.status_code} Service Unavailable during {action}: {resp.text}"
            )
        if resp.status_code == 404:
            raise FileNotFoundError(f"Google Drive resource not found during {action}: {resp.text}")
        if resp.status_code >= 400:
            raise ConnectorError(
                f"Google Drive API error ({resp.status_code}) during {action}: {resp.text}"
            )

    async def ensure_tenant_folder(
        self,
        folder_name: str,
        parent_folder_id: str | None = None,
        access_token: str | None = None,
    ) -> str:
        """Finds or creates a dedicated per-tenant root folder in Google Drive."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        q_parts = [
            f"name = '{folder_name}'",
            "mimeType = 'application/vnd.google-apps.folder'",
            "trashed = false",
        ]
        if parent_folder_id:
            q_parts.append(f"'{parent_folder_id}' in parents")
        query_str = " and ".join(q_parts)

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            resp = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers=headers,
                params={"q": query_str, "fields": "files(id, name, parents)"},
            )
            self._handle_error_response(resp, "ensure_tenant_folder.find")
            files = resp.json().get("files", [])
            if files:
                return files[0]["id"]

            create_body: dict[str, Any] = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_folder_id:
                create_body["parents"] = [parent_folder_id]

            resp_create = await client.post(
                "https://www.googleapis.com/drive/v3/files",
                headers=headers,
                json=create_body,
            )
            self._handle_error_response(resp_create, "ensure_tenant_folder.create")
            return resp_create.json()["id"]

    async def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str,
        folder_id: str | None = None,
        access_token: str | None = None,
    ) -> DriveFile:
        """Uploads a file strictly inside the tenant folder using multipart upload."""
        self._validate_file(content, mime_type)

        boundary = f"===============agentic_hr_{uuid.uuid4().hex}==============="
        metadata: dict[str, Any] = {"name": name}
        if folder_id:
            metadata["parents"] = [folder_id]

        metadata_json = json.dumps(metadata)
        body_parts = [
            f"--{boundary}\r\n".encode(),
            b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
            metadata_json.encode(),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            f"Content-Type: {mime_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        multipart_body = b"".join(body_parts)

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(multipart_body)),
        }

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            resp = await client.post(
                "https://www.googleapis.com/upload/drive/v3/files",
                params={
                    "uploadType": "multipart",
                    "fields": "id, name, mimeType, size, webViewLink, parents, createdTime, modifiedTime",
                },
                headers=headers,
                content=multipart_body,
            )
            self._handle_error_response(resp, "upload_file")
            data = resp.json()

            created_time = (
                datetime.fromisoformat(data["createdTime"])
                if data.get("createdTime")
                else datetime.now(UTC)
            )
            modified_time = (
                datetime.fromisoformat(data["modifiedTime"])
                if data.get("modifiedTime")
                else datetime.now(UTC)
            )
            size_bytes = int(data.get("size", len(content)))
            parents = data.get("parents", [])
            parent_id = parents[0] if parents else folder_id

            return DriveFile(
                file_id=data["id"],
                name=data.get("name", name),
                mime_type=data.get("mimeType", mime_type),
                size_bytes=size_bytes,
                web_view_link=data.get("webViewLink"),
                folder_id=parent_id,
                created_time=created_time,
                modified_time=modified_time,
            )

    async def download_file(
        self,
        file_id: str,
        access_token: str | None = None,
    ) -> bytes:
        """Downloads file binary bytes from Google Drive."""
        headers = {
            "Authorization": f"Bearer {access_token}",
        }
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params={"alt": "media"},
                headers=headers,
            )
            self._handle_error_response(resp, "download_file")
            return resp.content

    async def list_files(
        self,
        folder_id: str | None = None,
        page_size: int = 50,
        access_token: str | None = None,
    ) -> list[DriveFile]:
        """Lists files in the tenant folder created/opened by the app."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        q_parts = ["trashed = false"]
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        query_str = " and ".join(q_parts)

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            resp = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers=headers,
                params={
                    "q": query_str,
                    "pageSize": page_size,
                    "fields": "files(id, name, mimeType, size, webViewLink, parents, createdTime, modifiedTime)",
                },
            )
            self._handle_error_response(resp, "list_files")
            items = resp.json().get("files", [])
            result: list[DriveFile] = []
            for item in items:
                created_time = (
                    datetime.fromisoformat(item["createdTime"]) if item.get("createdTime") else None
                )
                modified_time = (
                    datetime.fromisoformat(item["modifiedTime"])
                    if item.get("modifiedTime")
                    else None
                )
                parents = item.get("parents", [])
                p_id = parents[0] if parents else folder_id
                result.append(
                    DriveFile(
                        file_id=item["id"],
                        name=item["name"],
                        mime_type=item["mimeType"],
                        size_bytes=int(item.get("size", 0)),
                        web_view_link=item.get("webViewLink"),
                        folder_id=p_id,
                        created_time=created_time,
                        modified_time=modified_time,
                    )
                )
            return result
