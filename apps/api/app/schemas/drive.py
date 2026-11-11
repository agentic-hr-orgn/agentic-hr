"""
Pydantic Schemas for Google Drive Connector Endpoints in Agentic HR Core.
Scope: https://www.googleapis.com/auth/drive.file per ADR 006.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DriveFolderEnsureRequest(BaseModel):
    folder_name: str = Field(default="Agentic HR Documents", min_length=1, max_length=255)
    parent_folder_id: str | None = None


class DriveFolderResponse(BaseModel):
    folder_id: str
    name: str
    web_view_link: str | None = None
    parent_folder_id: str | None = None


class DriveUploadRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content_base64: str = Field(..., description="Base64 encoded file content")
    mime_type: str = Field(..., min_length=3, max_length=128)
    folder_id: str | None = None


class DriveFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    drive_file_id: str
    folder_id: str | None = None
    name: str
    mime_type: str
    size_bytes: int
    web_view_link: str | None = None
    created_at: datetime
    updated_at: datetime


class DriveFileListResponse(BaseModel):
    files: list[DriveFileResponse]
    total: int
