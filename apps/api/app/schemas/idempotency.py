"""
Pydantic Schemas for Idempotency Records in Agentic HR Core.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IdempotencyRecordResponse(BaseModel):
    """Schema for reading an idempotency record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    key: str
    operation: str
    payload_hash: str
    status: str
    response_code: int | None = None
    response_body: dict[str, Any] | None = None
    created_at: datetime
    locked_at: datetime
    expires_at: datetime | None = None


class IdempotentResult(BaseModel):
    """Encapsulates the output of an idempotent operation."""

    response_code: int = Field(default=200, description="HTTP status code")
    response_body: dict[str, Any] = Field(default_factory=dict, description="Response payload")
    is_replay: bool = Field(
        default=False, description="Whether this response was served from cache"
    )
