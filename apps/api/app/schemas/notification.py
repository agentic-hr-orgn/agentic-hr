"""
Pydantic Schemas for Stage 4 In-App Notifications & Document Expiry Scanner APIs.
"""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.timezones import KarachiDatetime


class NotificationItem(BaseModel):
    """Serialized in-app notification record with Asia/Karachi timestamps."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    recipient_user_id: UUID | None = None
    recipient_email: str | None = None
    category: str
    severity: str
    title: str
    message: str
    entity_type: str | None = None
    entity_id: UUID | None = None
    dedupe_key: str | None = None
    is_read: bool
    read_at: KarachiDatetime | None = None
    created_at: KarachiDatetime


class NotificationListResponse(BaseModel):
    """Paginated notification list with unread count."""

    items: list[NotificationItem]
    total: int
    unread_count: int


class MarkAllReadResponse(BaseModel):
    """Result of marking all visible notifications as read."""

    updated: int


class TriggerExpiryScanRequest(BaseModel):
    """
    Optional request payload for manual expiry scan trigger.
    `reference_date` override is honored ONLY when ENVIRONMENT != 'production'.
    """

    reference_date: date | None = Field(default=None)


class ExpiryScanSummary(BaseModel):
    """Structured summary of a tenant document expiry scan."""

    tenant_id: UUID
    reference_date: str
    documents_matched: int
    alerts_created: int
    alerts_deduplicated: int
    emails_sent: int
    emails_skipped: int
