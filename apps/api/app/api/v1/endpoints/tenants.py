"""
Tenant Management & Kill-Switch Endpoints for Agentic HR Core.
Complies with ADR 002 (Tenancy), ADR 003, and Stage 2 Phase 10 specifications.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user_for_kill_switch,
)
from app.db.session import get_db
from app.models.tenant_entity import Tenant
from app.schemas.tenant import TenantKillSwitchRequest, TenantKillSwitchResponse
from app.services.audit_service import write_audit

router = APIRouter()


@router.post(
    "/{tenant_id}/kill-switch",
    response_model=TenantKillSwitchResponse,
    dependencies=[
        Depends(RateLimiter(requests=10, window_seconds=60, scope="tenants.kill_switch"))
    ],
)
async def toggle_tenant_kill_switch(
    tenant_id: UUID,
    payload: TenantKillSwitchRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user_for_kill_switch),
    db: AsyncSession = Depends(get_db),
) -> TenantKillSwitchResponse:
    """
    Engages or disengages the administrative kill-switch for a tenant.
    Enforces:
    1. Authorized administrator only (restricted strictly to organization Owners).
    2. Anti-Cross-Tenant Tampering: Verifies path tenant_id matches authenticated tenant.
    3. Authoritative database persistence in PostgreSQL.
    4. Immutable audit trail entry recorded in audit_logs before committing.
    """
    # 1. Role Authorization
    if current_user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized switch change: Only tenant Owners can toggle the kill-switch.",
        )

    # 2. Scope & Anti-Cross-Tenant Tampering (Do not trust tenant ID from browser)
    if str(tenant_id) != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant switch attempt: Cannot alter kill switch for another tenant.",
        )

    # 3. Load authoritative tenant entity from PostgreSQL
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    tenant = (await db.execute(stmt)).scalar_one_or_none()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )

    previous_state = {
        "is_suspended": tenant.is_suspended,
        "status": tenant.status,
        "suspended_at": tenant.suspended_at.isoformat() if tenant.suspended_at else None,
        "suspended_by": tenant.suspended_by,
        "suspension_reason": tenant.suspension_reason,
    }

    # 4. Mutate kill-switch state
    now = datetime.now(UTC)
    tenant.is_suspended = payload.is_suspended
    tenant.status = "suspended" if payload.is_suspended else "active"
    tenant.suspended_at = now if payload.is_suspended else None
    tenant.suspended_by = current_user.user_id if payload.is_suspended else None
    tenant.suspension_reason = payload.reason if payload.is_suspended else None
    tenant.updated_at = now

    # 5. Record immutable audit log entry in the same transaction
    action = "tenant.suspend" if payload.is_suspended else "tenant.restore"
    await write_audit(
        db,
        action=action,
        target_type="tenant",
        target_id=tenant.id,
        actor_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        details={
            "previous": previous_state,
            "is_suspended": payload.is_suspended,
            "status": tenant.status,
            "reason": payload.reason,
            "actor_email": current_user.email,
        },
    )

    await db.commit()

    return TenantKillSwitchResponse(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        is_suspended=tenant.is_suspended,
        status=tenant.status,
        suspended_at=tenant.suspended_at,
        suspended_by=tenant.suspended_by,
        suspension_reason=tenant.suspension_reason,
        updated_at=tenant.updated_at,
    )


@router.get(
    "/{tenant_id}/kill-switch",
    response_model=TenantKillSwitchResponse,
    dependencies=[
        Depends(RateLimiter(requests=30, window_seconds=60, scope="tenants.kill_switch_read"))
    ],
)
async def get_tenant_kill_switch_status(
    tenant_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user_for_kill_switch),
    db: AsyncSession = Depends(get_db),
) -> TenantKillSwitchResponse:
    """Retrieves authoritative kill-switch state for a tenant."""
    if current_user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized access: Only tenant Owners can inspect kill-switch status.",
        )

    if str(tenant_id) != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant access attempt: Cannot view kill switch for another tenant.",
        )

    stmt = select(Tenant).where(Tenant.id == tenant_id)
    tenant = (await db.execute(stmt)).scalar_one_or_none()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )

    return TenantKillSwitchResponse(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        is_suspended=tenant.is_suspended,
        status=tenant.status,
        suspended_at=tenant.suspended_at,
        suspended_by=tenant.suspended_by,
        suspension_reason=tenant.suspension_reason,
        updated_at=tenant.updated_at,
    )
