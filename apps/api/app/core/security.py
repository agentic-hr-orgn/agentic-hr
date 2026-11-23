"""
Multi-Tenant Authentication & Role-Based Access Control (RBAC) for Agentic HR Core.
Complies with ADR 002 (Tenancy), ADR 003, ADR 006, and Clerk JWT verification.
Authoritative authorization is resolved strictly from verified JWT claims + local PostgreSQL records.
"""

import logging
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.clerk import (
    DEFAULT_DEV_CLERK_ORG_ID,
    require_active_organization_claim,
    resolve_clerk_user_email,
    verify_clerk_token,
)
from app.core.config import settings
from app.db.session import get_db
from app.models.membership import Membership
from app.models.tenant import set_tenant_context
from app.models.tenant_entity import Tenant
from app.models.user import User

logger = logging.getLogger("agentic_hr.security")

# HTTP Bearer scheme with automatic error disabled to allow custom error handling
bearer_scheme = HTTPBearer(auto_error=False)


# --- Legacy / Stage 1 Tenant Context (Preserved for backward compatibility) ---
class TenantContext(BaseModel):
    tenant_id: str
    user_id: str | None = None
    role: str | None = None


async def get_current_tenant(
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
    authorization: str | None = Header(None),
) -> TenantContext:
    """
    Extract tenant context for multi-tenant isolation (ADR 002).
    Preserved for backward compatibility with Stage 1 synthetic routes.
    """
    tenant_id = x_tenant_id or "acme-tech-01"
    user_id = "user_synthetic_admin"
    role = "owner"
    return TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
    )


# --- Stage 2 Verified User & Tenancy Context ---
class AuthenticatedUserContext(BaseModel):
    """
    Authoritative authenticated context populated strictly from verified Clerk JWT
    and local PostgreSQL Tenant, User, Membership, and Role records.
    """

    user_id: str  # Local PostgreSQL User UUID (as string)
    clerk_user_id: str
    email: str
    name: str | None = None
    tenant_id: str  # Local PostgreSQL Tenant UUID (as string)
    clerk_org_id: str
    tenant_name: str
    role: str  # System role key: "owner", "manager", "employee"
    is_hr_head: bool = False
    is_payroll_admin: bool = False
    permissions: list[str] = []
    is_tenant_suspended: bool = False


async def _resolve_authenticated_context(
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
    request: Request | None = None,
    allow_suspended: bool = False,
) -> AuthenticatedUserContext:
    """
    Executes the authoritative 8-step authentication and multi-tenant resolution sequence:
    1. Extracts Bearer token and cryptographically verifies signature via Clerk JWKS.
    2. Enforces presence of active Clerk organization claim (org_id) and subject (sub).
    3. Resolves Tenant UUID via hardened SECURITY DEFINER lookup (resolve_tenant_by_clerk_org).
    4. Establishes transaction-local contexts: app.tenant_id and app.clerk_user_id.
    5. Resolves Tenant entity and evaluates PostgreSQL kill-switch state.
    6. Resolves User from PostgreSQL (enforced by users RLS).
    7. Resolves active Membership connecting user to that Tenant (enforced by memberships RLS).
    8. Assembles role and permission flags.
    """
    if credentials is None or not credentials.credentials:
        logger.debug("Missing or empty Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Step 1: Verify token signature and claims
    claims = verify_clerk_token(token)
    is_live_dev_fallback = bool(
        settings.is_non_production
        and settings.USE_FAKE_INTEGRATIONS
        and claims.get("_live_clerk_dev_jwt")
    )

    # Step 2: Enforce active organization claim and subject
    org_id = require_active_organization_claim(claims)

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token: missing subject claim.",
        )

    # Step 3: Narrow Tenant Resolution via Hardened Security Definer Function
    res = await db.execute(
        text("SELECT public.resolve_tenant_by_clerk_org(:org_id)"),
        {"org_id": org_id},
    )
    tenant_id = res.scalar()
    if not tenant_id and is_live_dev_fallback and org_id != DEFAULT_DEV_CLERK_ORG_ID:
        logger.info(
            f"Local dev Clerk org_id '{org_id}' not found in public.tenants; "
            f"mapping to seeded demo tenant '{DEFAULT_DEV_CLERK_ORG_ID}'."
        )
        org_id = DEFAULT_DEV_CLERK_ORG_ID
        res = await db.execute(
            text("SELECT public.resolve_tenant_by_clerk_org(:org_id)"),
            {"org_id": org_id},
        )
        tenant_id = res.scalar()

    if not tenant_id:
        logger.warning(f"Organization '{org_id}' not found or inactive in PostgreSQL")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Organization '{org_id}' is not registered or not active.",
        )

    # Step 4: Establish transaction-local tenant and user context in the current transaction
    await set_tenant_context(db, str(tenant_id), str(sub))

    # Step 5: Resolve Tenant entity (permitted by tenants RLS under app.tenant_id)
    stmt_tenant = select(Tenant).where(Tenant.id == tenant_id)
    tenant = (await db.execute(stmt_tenant)).scalar_one_or_none()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Organization '{org_id}' is not accessible.",
        )

    # Step 5b: Authoritative Database Kill-Switch Enforcement
    # If not specifically an admin/owner executing the kill-switch restore endpoint,
    # suspended tenants are strictly prohibited from accessing protected application operations.
    is_suspended = bool(tenant.is_suspended or tenant.status == "suspended")
    if is_suspended and not allow_suspended:
        logger.warning(
            f"Access rejected: Tenant {tenant.id} ('{tenant.name}') is suspended (kill-switch active)"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant organization is suspended. Access to protected operations is denied.",
        )

    # Step 6: Resolve User in local PostgreSQL records (permitted by users RLS under app.tenant_id / app.clerk_user_id)
    stmt_user = select(User).where((User.clerk_user_id == sub) | (User.email == sub))
    user = (await db.execute(stmt_user)).scalar_one_or_none()
    if not user and not claims.get("mock"):
        # Resolve verified email from JWT claims first, then Clerk Backend API for unlinked seeded users
        raw_email = (
            claims.get("email") or claims.get("primary_email") or claims.get("email_address")
        )
        resolved_email = (
            str(raw_email).strip().lower()
            if raw_email
            else await resolve_clerk_user_email(str(sub))
        )
        if resolved_email:
            stmt_email_user = select(User).where(func.lower(User.email) == resolved_email)
            user = (await db.execute(stmt_email_user)).scalar_one_or_none()
            if user is not None:
                await set_tenant_context(db, str(tenant_id), user.clerk_user_id)

    if not user:
        logger.warning(f"User '{sub}' not found in local PostgreSQL users")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{sub}' does not have an active platform account.",
        )

    # Step 7: Resolve active local Membership (permitted by memberships RLS under app.tenant_id)
    stmt_membership = (
        select(Membership)
        .options(joinedload(Membership.role))
        .where(
            Membership.tenant_id == tenant.id,
            Membership.user_id == user.id,
        )
    )
    membership = (await db.execute(stmt_membership)).scalar_one_or_none()
    if not membership:
        logger.warning(f"User '{user.email}' lacks membership in tenant '{tenant.name}'")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{user.email}' is not a member of organization '{tenant.name}'.",
        )
    if membership.status != "active":
        logger.warning(f"User '{user.email}' membership in '{tenant.name}' is {membership.status}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User membership in '{tenant.name}' is currently {membership.status}.",
        )

    # If the tenant is suspended and allow_suspended is True, enforce that only Owners can proceed
    if is_suspended and allow_suspended and membership.role.role_key != "owner":
        logger.warning(
            f"Non-owner '{user.email}' ({membership.role.role_key}) denied access to suspended tenant '{tenant.name}'"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized switch change: Only tenant Owners can toggle the kill-switch.",
        )

    # Step 8: Assemble permissions
    role_key = membership.role.role_key
    permissions = [role_key]
    if role_key == "owner":
        permissions.extend(["admin", "hr_head", "payroll_admin", "recruiter", "employee"])
    if membership.is_hr_head:
        permissions.append("hr_head")
    if membership.is_payroll_admin:
        permissions.append("payroll_admin")
    if role_key == "manager":
        permissions.extend(["recruiter", "manager"])
    if role_key == "employee":
        permissions.append("employee")

    context = AuthenticatedUserContext(
        user_id=str(user.id),
        clerk_user_id=user.clerk_user_id,
        email=user.email,
        name=user.name,
        tenant_id=str(tenant.id),
        clerk_org_id=tenant.clerk_org_id,
        tenant_name=tenant.name,
        role=role_key,
        is_hr_head=membership.is_hr_head,
        is_payroll_admin=membership.is_payroll_admin,
        permissions=sorted(set(permissions)),
        is_tenant_suspended=is_suspended,
    )

    if request is not None:
        request.state.current_user = context

    return context


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
) -> AuthenticatedUserContext:
    """
    Standard authenticated user dependency.
    Enforces that tenant is active and not kill-switched (fails closed with 403 if suspended).
    """
    return await _resolve_authenticated_context(
        credentials=credentials,
        db=db,
        request=request,
        allow_suspended=False,
    )


async def get_current_user_for_kill_switch(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
) -> AuthenticatedUserContext:
    """
    Specialized authentication dependency for kill-switch administration.
    Allows authenticated Owners of suspended tenants to reach the restore action.
    """
    return await _resolve_authenticated_context(
        credentials=credentials,
        db=db,
        request=request,
        allow_suspended=True,
    )


def require_role(allowed_roles: list[str]) -> Callable[..., AuthenticatedUserContext]:
    """
    Factory creating a FastAPI dependency enforcing that the authenticated user
    holds one of the specified business roles in their active tenant membership.
    """

    async def role_dependency(
        current_user: AuthenticatedUserContext = Depends(get_current_user),
    ) -> AuthenticatedUserContext:
        if current_user.role not in allowed_roles:
            logger.warning(
                f"User {current_user.email} with role '{current_user.role}' "
                f"denied access (required: {allowed_roles})"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: current role '{current_user.role}' is not in allowed roles: {', '.join(allowed_roles)}.",
            )
        return current_user

    return role_dependency


async def require_hr_head(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
) -> AuthenticatedUserContext:
    """Dependency verifying that the user has the HR Head flag (or owner role)."""
    if not (current_user.is_hr_head or current_user.role == "owner"):
        logger.warning(f"User {current_user.email} lacks HR Head privileges")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: HR Head privileges required.",
        )
    return current_user


async def require_owner_manager_or_hr_head(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
) -> AuthenticatedUserContext:
    """Dependency allowing Owner, Manager, or HR Head roles (blocks standard employee role)."""
    if not (current_user.role in ("owner", "manager") or current_user.is_hr_head):
        logger.warning(
            f"User {current_user.email} with role '{current_user.role}' "
            "denied access (required: owner, manager, or hr_head)"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Owner, Manager, or HR Head privileges required.",
        )
    return current_user


async def require_payroll_admin(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
) -> AuthenticatedUserContext:
    """Dependency verifying that the user has the Payroll Admin flag (or owner role)."""
    if not (current_user.is_payroll_admin or current_user.role == "owner"):
        logger.warning(f"User {current_user.email} lacks Payroll Admin privileges")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Payroll Admin privileges required.",
        )
    return current_user
