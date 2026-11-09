"""
Audit Log Query Endpoints for Agentic HR Core.
Allows tenant Owners and authorized administrators to inspect the tenant's audit trail.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUserContext, require_role
from app.db.session import get_db
from app.services.audit_service import get_tenant_audit_logs

router = APIRouter()


class AuditLogResponse(BaseModel):
    id: str
    tenant_id: str
    actor_id: str | None
    action: str
    target_type: str
    target_id: str | None
    timestamp: datetime
    details: dict[str, Any]
    request_id: str | None


@router.get("/logs", response_model=list[AuditLogResponse])
async def list_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: AuthenticatedUserContext = Depends(require_role(["owner"])),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLogResponse]:
    """
    Returns audit logs for the authenticated tenant.
    Restricted to tenant Owners. Subject to PostgreSQL Row-Level Security.
    """
    logs = await get_tenant_audit_logs(db, limit=limit, offset=offset)
    return [
        AuditLogResponse(
            id=str(log.id),
            tenant_id=str(log.tenant_id),
            actor_id=str(log.actor_id) if log.actor_id else None,
            action=log.action,
            target_type=log.target_type,
            target_id=log.target_id,
            timestamp=log.timestamp,
            details=log.details,
            request_id=log.request_id,
        )
        for log in logs
    ]
