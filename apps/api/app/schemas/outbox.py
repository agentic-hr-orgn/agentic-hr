"""
Pydantic Schemas for Outbox Events in Agentic HR Core.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OutboxEventBase(BaseModel):
    """Base schema for Outbox events."""

    event_type: str = Field(..., max_length=128, description="Domain event category")
    event_payload: dict[str, Any] = Field(..., description="Canonical event payload dictionary")


class OutboxEventCreate(OutboxEventBase):
    """Schema for creating a new outbox event."""

    tenant_id: UUID
    next_attempt_at: datetime | None = None


class OutboxEventResponse(OutboxEventBase):
    """Schema for reading an outbox event."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    status: str
    attempts: int
    created_at: datetime
    processed_at: datetime | None = None
    next_attempt_at: datetime
    last_error: str | None = None
