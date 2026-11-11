"""
Pydantic Schemas for Connector Endpoints in Agentic HR Core.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnectorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    provider: str
    account_email: str
    scopes: list[str]
    status: str
    is_read_only: bool
    jobs_paused: bool
    token_expiry: datetime | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ConnectDirectRequest(BaseModel):
    provider: str = Field(default="google")
    account_email: str
    scopes: list[str]
    access_token: str
    refresh_token: str | None = None
    token_expiry: datetime | None = None


class ReadOnlyToggleRequest(BaseModel):
    is_read_only: bool


class SendEmailRequest(BaseModel):
    to_email: str
    subject: str | None = None
    body_text: str | None = None
    template_name: str | None = None
    template_variables: dict[str, Any] | None = None
    credential_id: UUID | None = None
    force_retry: bool = False


class BounceWebhookRequest(BaseModel):
    provider_message_id: str
    error_message: str


class SentMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    credential_id: UUID | None = None
    idempotency_key: str | None = None
    recipient_email: str
    subject: str
    template_name: str | None = None
    provider_message_id: str | None = None
    status: str
    error_message: str | None = None
    created_at: datetime
