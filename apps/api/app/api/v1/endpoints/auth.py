"""
Authentication & Authorization Endpoints for Agentic HR Core.
Exposes /api/v1/auth/me to return verified user, active tenant, role, and permission flags.
Provides role-testing endpoints for RBAC verification.
Provides membership management endpoint enforcing anti-self-promotion rules.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
    require_payroll_admin,
    require_role,
)
from app.db.session import get_db
from app.models.membership import Membership
from app.models.role import Role
from app.services.audit_service import write_audit

router = APIRouter()


class MembershipUpdateRequest(BaseModel):
    role_key: str | None = None
    is_hr_head: bool | None = None
    is_payroll_admin: bool | None = None


class MembershipResponse(BaseModel):
    membership_id: str
    user_id: str
    tenant_id: str
    role_key: str
    is_hr_head: bool
    is_payroll_admin: bool
    status: str


@router.get("/me", response_model=AuthenticatedUserContext)
async def get_me(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
) -> AuthenticatedUserContext:
    """
    Returns the currently authenticated user's profile, active organization tenant,
    resolved business role, and system permissions.
    """
    return current_user


@router.get("/test/owner")
async def test_owner_access(
    current_user: AuthenticatedUserContext = Depends(require_role(["owner"])),
) -> dict[str, str]:
    """Test endpoint restricted strictly to organization Owners."""
    return {
        "status": "granted",
        "role": current_user.role,
        "message": f"Welcome Owner {current_user.email}",
    }


@router.get("/test/manager")
async def test_manager_access(
    current_user: AuthenticatedUserContext = Depends(require_role(["owner", "manager"])),
) -> dict[str, str]:
    """Test endpoint accessible by Managers and Owners."""
    return {
        "status": "granted",
        "role": current_user.role,
        "message": f"Welcome Manager/Owner {current_user.email}",
    }


@router.get("/test/hr-head")
async def test_hr_head_access(
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
) -> dict[str, str]:
    """Test endpoint accessible by users with HR Head permission flag or Owner."""
    return {
        "status": "granted",
        "is_hr_head": str(current_user.is_hr_head),
        "message": f"HR Head access granted for {current_user.email}",
    }


@router.get("/test/payroll-admin")
async def test_payroll_admin_access(
    current_user: AuthenticatedUserContext = Depends(require_payroll_admin),
) -> dict[str, str]:
    """Test endpoint accessible by users with Payroll Admin permission flag or Owner."""
    return {
        "status": "granted",
        "is_payroll_admin": str(current_user.is_payroll_admin),
        "message": f"Payroll Admin access granted for {current_user.email}",
    }


@router.patch("/memberships/{membership_id}", response_model=MembershipResponse)
async def update_membership(
    membership_id: UUID,
    payload: MembershipUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_role(["owner"])),
    db: AsyncSession = Depends(get_db),
) -> MembershipResponse:
    """
    Updates role and permission flags on a membership within the active tenant.
    Restricted strictly to Owners.
    Enforces anti-self-promotion: Users cannot change their own role or permissions.
    """
    stmt = (
        select(Membership)
        .options(joinedload(Membership.role))
        .where(Membership.id == membership_id)
    )
    membership = (await db.execute(stmt)).scalar_one_or_none()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership record not found.",
        )

    # Anti-Self-Promotion Guard: Cannot alter your own role or flags
    if str(membership.user_id) == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot alter your own membership role or permission flags.",
        )

    # Capture state before mutation
    previous_state = {
        "role_key": membership.role.role_key if membership.role else None,
        "is_hr_head": membership.is_hr_head,
        "is_payroll_admin": membership.is_payroll_admin,
    }

    if payload.role_key is not None:
        stmt_role = select(Role).where(Role.role_key == payload.role_key)
        new_role = (await db.execute(stmt_role)).scalar_one_or_none()
        if not new_role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role key '{payload.role_key}'.",
            )
        membership.role_id = new_role.id
        membership.role = new_role

    if payload.is_hr_head is not None:
        membership.is_hr_head = payload.is_hr_head

    if payload.is_payroll_admin is not None:
        membership.is_payroll_admin = payload.is_payroll_admin

    new_state = {
        "role_key": membership.role.role_key if membership.role else None,
        "is_hr_head": membership.is_hr_head,
        "is_payroll_admin": membership.is_payroll_admin,
    }

    # Record immutable audit entry in the same transaction
    await write_audit(
        db,
        action="membership.update",
        target_type="membership",
        target_id=membership.id,
        actor_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        details={
            "previous": previous_state,
            "updated": new_state,
            "target_user_id": str(membership.user_id),
        },
    )

    await db.commit()

    return MembershipResponse(
        membership_id=str(membership.id),
        user_id=str(membership.user_id),
        tenant_id=str(membership.tenant_id),
        role_key=membership.role.role_key,
        is_hr_head=membership.is_hr_head,
        is_payroll_admin=membership.is_payroll_admin,
        status=membership.status,
    )
