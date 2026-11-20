"""
Pydantic Schemas for Stage 4 Policy Document Upload Stub API.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PolicyDocumentResponse(BaseModel):
    """Serialized policy document metadata response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    title: str
    file_name: str
    mime_type: str
    size_bytes: int
    storage_provider: str
    storage_key: str
    ingestion_status: str
    uploaded_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
