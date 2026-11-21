"""
Connector Management & External Send REST API for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and ADR 006 (Google Workspace Integration).

Endpoints:
- GET /connectors/google/oauth/authorize: Generates Google OAuth URL with signed state token.
- GET /connectors/google/oauth/callback: Handles OAuth code exchange and encrypted token persistence.
- POST /connectors/google/connect: Direct credential connection (for synthetic testing).
- GET /connectors: Lists connected credentials for current tenant (redacts secrets).
- PATCH /connectors/{id}/read-only: Toggles client-enforced read-only mode.
- POST /connectors/{id}/refresh: Manually refreshes OAuth access token.
- DELETE /connectors/{id}: Revokes credential and pauses jobs.
- POST /connectors/email/send: Sends outbound email with idempotency and rate limiting.
- POST /connectors/email/bounces: Synchronously records bounce notification without inbox polling.
- GET /connectors/email/sent: Lists sent messages for active tenant.
- GET /connectors/email/messages: Lists sent messages with filtering and pagination.
- POST /connectors/sheets/sync: One-way sync queue processing (Postgres -> Sheets).
- GET /connectors/sheets/drift: Lists detected drift logs.
- POST /connectors/sheets/reconcile: Drift reconciliation (Postgres overwrites Sheets).
- POST /connectors/drive/folders: Finds or creates tenant Drive folder under drive.file.
- POST /connectors/drive/upload: Uploads file to tenant Drive folder with MIME/size validation.
- GET /connectors/drive/files: Lists files in tenant Drive folder.
- GET /connectors/drive/files/{file_id}/download: Downloads file binary bytes.
- POST /connectors/calendar/free-busy: Queries free/busy slots in Asia/Karachi timezone.
- POST /connectors/calendar/events: Creates meeting/interview event with Google Meet link.
- PATCH /connectors/calendar/events/{event_id}: Reschedules or updates calendar event.
- DELETE /connectors/calendar/events/{event_id}: Cancels/deletes calendar event.
"""

import base64
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import AuthenticatedUserContext, get_current_user, require_role
from app.db.session import get_db
from app.integrations.base import GmailDeliveryError
from app.models.connector import SentMessage
from app.models.sheets import SheetsDriftLog
from app.models.tenant import set_tenant_context
from app.schemas.calendar import (
    CalendarEventCreateRequest,
    CalendarEventResponse,
    CalendarEventUpdateRequest,
    FreeBusyQueryRequest,
    FreeBusyQueryResponse,
    TimeSlotSchema,
)
from app.schemas.connector import (
    BounceWebhookRequest,
    ConnectDirectRequest,
    ConnectorResponse,
    ReadOnlyToggleRequest,
    SendEmailRequest,
    SentMessageResponse,
)
from app.schemas.drive import (
    DriveFileListResponse,
    DriveFileResponse,
    DriveFolderEnsureRequest,
    DriveFolderResponse,
    DriveUploadRequest,
)
from app.schemas.sheets import (
    SheetsDriftLogResponse,
    SheetsReconcileResponse,
    SheetsSyncRequest,
    SheetsSyncResponse,
)
from app.services.audit_service import write_audit
from app.services.calendar_service import CalendarSchedulingService
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorError,
    ConnectorService,
    ConnectorServiceUnavailableError,
    CredentialNotFoundError,
    InvalidScopeError,
    OAuthStateInvalidError,
    ProhibitedScopeError,
    ReadOnlyConnectorError,
    build_google_authorization_url,
    verify_oauth_state,
)
from app.services.drive_service import DriveManagerService
from app.services.email_delivery_service import EmailDeliveryService, RecipientBlockedError
from app.services.sheets_sync_service import SheetsSyncService

logger = logging.getLogger("agentic_hr.api.connectors")

router = APIRouter()


# ==============================================================================
# 1. OAuth Authorization & Callback
# ==============================================================================


@router.get("/google/authorize")
@router.get("/google/oauth/authorize")
async def google_oauth_authorize(
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
) -> dict[str, str]:
    """Generates an offline-access Google OAuth URL with a signed CSRF state token."""
    auth_url = build_google_authorization_url(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
    )
    return {"authorization_url": auth_url}


@router.get("/google/callback")
@router.get("/google/oauth/callback")
async def google_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Handles Google OAuth redirect, validates CSRF state, exchanges authorization code,
    verifies scopes against prohibited list, and stores encrypted credentials.
    """
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth authorization failed from Google: {error}",
        )

    # 1. Validate signed CSRF state
    try:
        tenant_id, user_id = verify_oauth_state(state)
    except OAuthStateInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or expired OAuth state: {exc}",
        ) from exc

    # 2. Set database tenant context
    await set_tenant_context(db, str(tenant_id), str(user_id))

    # 3. Exchange code for tokens
    if settings.USE_FAKE_INTEGRATIONS:
        # Synthetic / Sandbox Mode
        account_email = "agentic.hr.test.ubaid.owner@gmail.com"
        scopes = [
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/calendar.events",
        ]
        access_token = f"fake_oauth_access_{code}"
        refresh_token = f"fake_oauth_refresh_{code}"
    else:
        # Real Google OAuth Token Exchange
        token_url = "https://oauth2.googleapis.com/token"
        payload = {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(token_url, data=payload)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to exchange OAuth code: {resp.text}",
                )
            token_data = resp.json()
            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token")
            scopes = token_data.get("scope", "").split(" ")

            # Fetch authenticated user profile to get email
            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            account_email = userinfo_resp.json().get("email", "unknown@gmail.com")

    # 4. Store credentials with AES-256-GCM envelope encryption & scope validation
    try:
        cred = await ConnectorService.store_credentials(
            session=db,
            tenant_id=tenant_id,
            provider="google",
            account_email=account_email,
            scopes=scopes,
            access_token=access_token,
            refresh_token=refresh_token,
            actor_id=user_id,
        )
        await db.commit()
    except ProhibitedScopeError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except InvalidScopeError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {
        "status": "connected",
        "tenant_id": str(tenant_id),
        "credential_id": str(cred.id),
        "provider": "google",
        "account_email": account_email,
    }


# ==============================================================================
# 2. Manual Connect & Credential Management
# ==============================================================================


@router.post("/google/connect", response_model=ConnectorResponse)
async def connect_google_direct(
    request: ConnectDirectRequest,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse:
    """Directly registers or updates credentials (used for synthetic testing & sandboxes)."""
    try:
        cred = await ConnectorService.store_credentials(
            session=db,
            tenant_id=current_user.tenant_id,
            provider=request.provider,
            account_email=request.account_email,
            scopes=request.scopes,
            access_token=request.access_token,
            refresh_token=request.refresh_token,
            token_expiry=request.token_expiry,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return ConnectorResponse.model_validate(cred)
    except ProhibitedScopeError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except InvalidScopeError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConnectorResponse]:
    """Lists all configured connectors for the caller's tenant."""
    creds = await ConnectorService.list_credentials(db, current_user.tenant_id)
    if not creds and settings.USE_FAKE_INTEGRATIONS and settings.is_non_production:
        # Dev / Sandbox auto-seeding (ADR 006)
        account_email = "agentic.hr.test.ubaid.owner@gmail.com"
        scopes = [
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/calendar.events",
        ]
        cred = await ConnectorService.store_credentials(
            session=db,
            tenant_id=current_user.tenant_id,
            provider="google",
            account_email=account_email,
            scopes=scopes,
            access_token="fake_oauth_access_seed",
            refresh_token="fake_oauth_refresh_seed",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
            actor_id=current_user.user_id,
        )
        await db.commit()
        creds = [cred]
    return [ConnectorResponse.model_validate(c) for c in creds]


@router.patch("/{id}/read-only", response_model=ConnectorResponse)
async def toggle_connector_read_only(
    id: UUID,
    request: ReadOnlyToggleRequest,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse:
    """Toggles client-enforced read-only mode for a connector."""
    try:
        cred = await ConnectorService.set_read_only(
            session=db,
            tenant_id=current_user.tenant_id,
            credential_id=id,
            is_read_only=request.is_read_only,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return ConnectorResponse.model_validate(cred)
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{id}/refresh", response_model=ConnectorResponse)
async def refresh_connector_token(
    id: UUID,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse:
    """Manually refreshes OAuth access token using stored encrypted refresh token."""
    try:
        cred = await ConnectorService.get_credential_by_id(db, current_user.tenant_id, id)
        await ConnectorService.refresh_access_token(db, cred, actor_id=current_user.user_id)
        await db.commit()
        return ConnectorResponse.model_validate(cred)
    except ConnectorAuthExpiredError as exc:
        # Persist needs_reconnect + jobs_paused + audit log before raising 401
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except ConnectorServiceUnavailableError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"CONNECTOR_UNAVAILABLE: {exc}",
        ) from exc
    except ConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"CONNECTOR_ERROR: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{id}/reconnect", response_model=ConnectorResponse)
@router.post("/google/reconnect", response_model=ConnectorResponse)
async def reconnect_connector(
    id: UUID | None = None,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse | JSONResponse:
    """
    Restores status to connected and unpauses jobs in fake/sandbox mode.
    When USE_FAKE_INTEGRATIONS=false, returns HTTP 409 OAUTH_REAUTH_REQUIRED with a
    signed Google OAuth authorization_url so real grants are never restored without consent.
    """
    try:
        if id:
            cred = await ConnectorService.get_credential_by_id(db, current_user.tenant_id, id)
        else:
            creds = await ConnectorService.list_credentials(db, current_user.tenant_id)
            if not creds:
                raise CredentialNotFoundError("No connector found to reconnect.")
            cred = creds[0]

        if not settings.USE_FAKE_INTEGRATIONS:
            auth_url = build_google_authorization_url(
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
            )
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "error": {
                        "code": "OAUTH_REAUTH_REQUIRED",
                        "message": "Reconnecting a live Google Workspace credential requires fresh OAuth consent.",
                        "authorization_url": auth_url,
                    },
                    "code": "OAUTH_REAUTH_REQUIRED",
                    "authorization_url": auth_url,
                },
            )

        cred.status = "connected"
        cred.jobs_paused = False
        cred.error_message = None
        cred.token_expiry = datetime.now(UTC) + timedelta(hours=1)
        cred.updated_at = datetime.now(UTC)
        await db.flush()
        await write_audit(
            db,
            action="connector.reconnected",
            target_type="connector_credential",
            target_id=cred.id,
            details={
                "provider": cred.provider,
                "account_email": cred.account_email,
                "status": "connected",
            },
            actor_id=current_user.user_id,
            tenant_id=current_user.tenant_id,
        )
        await db.commit()
        return ConnectorResponse.model_validate(cred)
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete("/{id}")
async def revoke_connector(
    id: UUID,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner"])),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Revokes a connector credential (restricted strictly to Owners)."""
    try:
        await ConnectorService.revoke_credential(
            session=db,
            tenant_id=current_user.tenant_id,
            credential_id=id,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return {"status": "revoked", "id": str(id)}
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


# ==============================================================================
# 3. Email Delivery Operations (Send-Only ADR 006)
# ==============================================================================


@router.post("/email/send", response_model=dict[str, Any])
async def send_email(
    request: SendEmailRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Sends an outbound email via Gmail least-privilege send connector.
    Enforces per-tenant rate limits, read-only switch, and IdempotencyGate.
    """
    effective_idempotency_key = idempotency_key or x_idempotency_key
    try:
        result = await EmailDeliveryService.send_email(
            session=db,
            tenant_id=current_user.tenant_id,
            to_email=request.to_email,
            subject=request.subject,
            body_text=request.body_text,
            template_name=request.template_name,
            template_variables=request.template_variables,
            idempotency_key=effective_idempotency_key,
            credential_id=request.credential_id,
            actor_id=current_user.user_id,
            force_retry=request.force_retry,
        )
        await db.commit()
        return result
    except RecipientBlockedError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"RECIPIENT_BLOCKED: {exc}",
        ) from exc
    except ReadOnlyConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CONNECTOR_READ_ONLY: {exc}",
        ) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except GmailDeliveryError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"EMAIL_DELIVERY_FAILED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/email/bounces")
async def record_email_bounce(
    request: BounceWebhookRequest,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Records an incoming bounce or delivery failure synchronously.
    Complies with ADR 006: marks status without requiring inbox-reading scopes.
    """
    record = await EmailDeliveryService.record_bounce(
        session=db,
        tenant_id=current_user.tenant_id,
        provider_message_id=request.provider_message_id,
        error_message=request.error_message,
        actor_id=current_user.user_id,
    )
    await db.commit()
    return {"status": "bounced", "message_id": str(record.id)}


@router.get("/email/sent", response_model=list[SentMessageResponse])
async def list_sent_messages(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SentMessageResponse]:
    """Lists all outbound sent messages for the active tenant."""
    stmt = (
        select(SentMessage)
        .where(SentMessage.tenant_id == UUID(current_user.tenant_id))
        .order_by(desc(SentMessage.created_at))
        .limit(50)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()
    return [SentMessageResponse.model_validate(m) for m in messages]


@router.get("/email/messages", response_model=list[SentMessageResponse])
async def list_email_messages(
    status_filter: str | None = Query(None, alias="status"),
    recipient: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SentMessageResponse]:
    """Lists sent messages with status filtering and pagination for active tenant."""
    stmt = select(SentMessage).where(SentMessage.tenant_id == UUID(current_user.tenant_id))
    if status_filter:
        stmt = stmt.where(SentMessage.status == status_filter.strip().lower())
    if recipient:
        stmt = stmt.where(func.lower(SentMessage.recipient_email) == recipient.strip().lower())

    stmt = stmt.order_by(desc(SentMessage.created_at)).limit(limit).offset(offset)
    result = await db.execute(stmt)
    messages = result.scalars().all()
    return [SentMessageResponse.model_validate(m) for m in messages]


# ==============================================================================
# 4. Google Sheets Operations (Synced View ADR 005)
# ==============================================================================


@router.post("/sheets/sync", response_model=SheetsSyncResponse)
async def sync_sheets_queue(
    request: SheetsSyncRequest = SheetsSyncRequest(),
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> SheetsSyncResponse:
    """
    Executes one-way batch synchronization from PostgreSQL to Google Sheets.
    Pulls pending queue records using SELECT ... FOR UPDATE SKIP LOCKED.
    Enforces per-tenant rate limit and client-side read-only switch.
    """
    try:
        response = await SheetsSyncService.process_sync_queue(
            session=db,
            tenant_id=current_user.tenant_id,
            batch_size=request.batch_size,
            spreadsheet_id=request.spreadsheet_id,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return response
    except ReadOnlyConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CONNECTOR_READ_ONLY: {exc}",
        ) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/sheets/drift", response_model=list[SheetsDriftLogResponse])
async def list_sheets_drift_logs(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SheetsDriftLogResponse]:
    """Lists all detected drift logs between PostgreSQL and Google Sheets for active tenant."""
    stmt = (
        select(SheetsDriftLog)
        .where(SheetsDriftLog.tenant_id == UUID(current_user.tenant_id))
        .order_by(desc(SheetsDriftLog.created_at))
    )
    result = await db.execute(stmt)
    records = result.scalars().all()
    return [SheetsDriftLogResponse.model_validate(r) for r in records]


@router.post("/sheets/reconcile", response_model=SheetsReconcileResponse)
async def reconcile_sheets_drift(
    request: SheetsSyncRequest = SheetsSyncRequest(),
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> SheetsReconcileResponse:
    """
    Scans Google Sheets and compares against PostgreSQL authoritative state.
    Detects unauthorized human modifications in Sheets, logs discrepancies to sheets_drift_logs,
    and overwrites drifted Sheet cells with PostgreSQL truth.
    """
    try:
        response = await SheetsSyncService.reconcile_drift(
            session=db,
            tenant_id=current_user.tenant_id,
            spreadsheet_id=request.spreadsheet_id,
            sheet_name=request.sheet_name,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return response
    except ReadOnlyConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CONNECTOR_READ_ONLY: {exc}",
        ) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


# ==============================================================================
# 5. Google Drive Operations (drive.file scope)
# ==============================================================================


@router.post("/drive/folders", response_model=DriveFolderResponse)
async def ensure_drive_folder(
    request: DriveFolderEnsureRequest = DriveFolderEnsureRequest(),
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> DriveFolderResponse:
    """Finds or creates a dedicated tenant root folder in Google Drive under drive.file."""
    try:
        folder_id = await DriveManagerService.ensure_tenant_folder(
            session=db,
            tenant_id=current_user.tenant_id,
            folder_name=request.folder_name,
            parent_folder_id=request.parent_folder_id,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return DriveFolderResponse(
            folder_id=folder_id,
            name=request.folder_name,
            parent_folder_id=request.parent_folder_id,
        )
    except ReadOnlyConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CONNECTOR_READ_ONLY: {exc}",
        ) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/drive/upload", response_model=DriveFileResponse)
async def upload_drive_file(
    request: DriveUploadRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> DriveFileResponse:
    """Uploads a file to Google Drive strictly inside the tenant folder with MIME/size validation."""
    effective_idempotency_key = idempotency_key or x_idempotency_key
    try:
        content = base64.b64decode(request.content_base64)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base64 encoding in content_base64: {exc}",
        ) from exc

    try:
        record = await DriveManagerService.upload_file(
            session=db,
            tenant_id=current_user.tenant_id,
            name=request.name,
            content=content,
            mime_type=request.mime_type,
            folder_id=request.folder_id,
            idempotency_key=effective_idempotency_key,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return DriveFileResponse.model_validate(record)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ReadOnlyConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CONNECTOR_READ_ONLY: {exc}",
        ) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/drive/files", response_model=DriveFileListResponse)
async def list_drive_files(
    folder_id: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=100),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DriveFileListResponse:
    """Lists files owned by the tenant in Google Drive."""
    records = await DriveManagerService.list_files(
        session=db,
        tenant_id=current_user.tenant_id,
        folder_id=folder_id,
        page_size=page_size,
        actor_id=current_user.user_id,
    )
    return DriveFileListResponse(
        files=[DriveFileResponse.model_validate(r) for r in records],
        total=len(records),
    )


@router.get("/drive/files/{file_id}/download")
async def download_drive_file(
    file_id: str,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Downloads binary file bytes from Google Drive."""
    try:
        content, record = await DriveManagerService.download_file(
            session=db,
            tenant_id=current_user.tenant_id,
            file_id=file_id,
            actor_id=current_user.user_id,
        )
        media_type = record.mime_type if record else "application/octet-stream"
        filename = record.name if record else f"file_{file_id}"
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# ==============================================================================
# 6. Google Calendar Operations (calendar.events scope & Asia/Karachi timezone)
# ==============================================================================


@router.post("/calendar/free-busy", response_model=FreeBusyQueryResponse)
async def query_calendar_free_busy(
    request: FreeBusyQueryRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FreeBusyQueryResponse:
    """Queries free/busy windows for calendar IDs in Asia/Karachi timezone."""
    try:
        free_busy_map = await CalendarSchedulingService.get_free_busy(
            session=db,
            tenant_id=current_user.tenant_id,
            calendar_ids=request.calendar_ids,
            time_min=request.time_min,
            time_max=request.time_max,
            actor_id=current_user.user_id,
        )
        calendars_dict = {
            cid: [TimeSlotSchema(start_time=s.start_time, end_time=s.end_time) for s in slots]
            for cid, slots in free_busy_map.items()
        }
        return FreeBusyQueryResponse(calendars=calendars_dict)
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/calendar/events", response_model=CalendarEventResponse)
async def create_calendar_event(
    request: CalendarEventCreateRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> CalendarEventResponse:
    """Creates a calendar event with Meet link in Asia/Karachi timezone."""
    effective_idempotency_key = idempotency_key or x_idempotency_key
    try:
        record = await CalendarSchedulingService.create_event(
            session=db,
            tenant_id=current_user.tenant_id,
            title=request.title,
            start_time=request.start_time,
            end_time=request.end_time,
            attendees=request.attendees,
            description=request.description,
            location=request.location,
            calendar_id=request.calendar_id,
            timezone=request.timezone,
            idempotency_key=effective_idempotency_key,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return CalendarEventResponse.model_validate(record)
    except ReadOnlyConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CONNECTOR_READ_ONLY: {exc}",
        ) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/calendar/events/{event_id}", response_model=CalendarEventResponse)
async def update_calendar_event(
    event_id: str,
    request: CalendarEventUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> CalendarEventResponse:
    """Updates/reschedules an existing calendar event in Asia/Karachi timezone."""
    try:
        record = await CalendarSchedulingService.update_event(
            session=db,
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            title=request.title,
            start_time=request.start_time,
            end_time=request.end_time,
            attendees=request.attendees,
            description=request.description,
            location=request.location,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return CalendarEventResponse.model_validate(record)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ReadOnlyConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CONNECTOR_READ_ONLY: {exc}",
        ) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/calendar/events/{event_id}")
async def cancel_calendar_event(
    event_id: str,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Cancels/deletes an existing calendar event."""
    try:
        deleted = await CalendarSchedulingService.cancel_event(
            session=db,
            tenant_id=current_user.tenant_id,
            event_id=event_id,
            actor_id=current_user.user_id,
        )
        await db.commit()
        return {"deleted": deleted, "event_id": event_id}
    except ReadOnlyConnectorError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"CONNECTOR_READ_ONLY: {exc}",
        ) from exc
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        ) from exc
    except CredentialNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
