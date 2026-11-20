"""
In-App Notifications & Document Expiry Scanner REST API Endpoints (Stage 4 Prompt 5).

Endpoints:
- GET   /api/v1/notifications
- POST  /api/v1/notifications/read-all
- POST  /api/v1/notifications/trigger-expiry-scan
- PATCH /api/v1/notifications/{notification_id}/read
"""

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
)
from app.db.session import get_db
from app.schemas.notification import (
    ExpiryScanSummary,
    MarkAllReadResponse,
    NotificationItem,
    NotificationListResponse,
    TriggerExpiryScanRequest,
)
from app.services.document_expiry_service import DocumentExpiryService
from app.services.notification_service import NotificationService

router = APIRouter()


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(False),
    category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    """
    Lists visible in-app notifications for the caller's tenant along with total and unread_count.
    Owners and HR Heads see all tenant notifications; other users see only their own.
    """
    return await NotificationService.list_notifications(
        session=db,
        tenant_id=current_user.tenant_id,
        actor=current_user,
        unread_only=unread_only,
        category=category,
        limit=limit,
        offset=offset,
    )


@router.post("/read-all", response_model=MarkAllReadResponse)
async def mark_all_notifications_read(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarkAllReadResponse:
    """Marks all unread notifications visible to the caller as read."""
    updated = await NotificationService.mark_all_read(
        session=db,
        tenant_id=current_user.tenant_id,
        actor=current_user,
    )
    await db.commit()
    return MarkAllReadResponse(updated=updated)


@router.post("/trigger-expiry-scan", response_model=ExpiryScanSummary)
async def trigger_expiry_scan(
    payload: TriggerExpiryScanRequest | None = Body(default=None),
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> ExpiryScanSummary:
    """
    Manually runs the 30/7/1-day document expiry scan in-process for the caller's tenant.
    Restricted to Owner and HR Head. Optional `reference_date` override is honored ONLY
    when `ENVIRONMENT != 'production'`.
    """
    ref_date = None
    if payload is not None and payload.reference_date is not None and settings.is_non_production:
        ref_date = payload.reference_date

    return await DocumentExpiryService.scan_expiring_documents_for_tenant(
        session=db,
        tenant_id=current_user.tenant_id,
        reference_date_pkt=ref_date,
        actor_id=current_user.user_id,
        clerk_user_id=current_user.clerk_user_id,
        send_emails=True,
    )


@router.patch("/{notification_id}/read", response_model=NotificationItem)
async def mark_notification_read(
    notification_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationItem:
    """Marks a single visible notification as read."""
    item = await NotificationService.mark_read(
        session=db,
        tenant_id=current_user.tenant_id,
        notification_id=notification_id,
        actor=current_user,
    )
    await db.commit()
    return item
