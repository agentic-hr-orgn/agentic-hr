"""
Multi-Tenant Authentication & Role-Based Access Control (RBAC) for Agentic HR Core.
Complies with ADR 002 (Tenancy), ADR 003, ADR 006, and Clerk JWT verification.
Authoritative authorization is resolved strictly from verified JWT claims + local PostgreSQL records.
"""

import logging
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.clerk import verify_clerk_token
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedUserContext:
    """
    FastAPI dependency executing the exact non-circular 7-step sequence in one transaction:
    1. Extracts Bearer token and cryptographically verifies signature via Clerk JWKS.
    2. Enforces presence of active Clerk organization claim (org_id) and subject (sub).
    3. Resolves Tenant UUID via hardened SECURITY DEFINER lookup (resolve_tenant_by_clerk_org).
    4. Establishes transaction-local contexts: app.tenant_id and app.clerk_user_id.
    5. Resolves User from PostgreSQL (enforced by users RLS).
    6. Resolves active Membership connecting user to that Tenant (enforced by memberships RLS).
    7. Loads business role and permission flags (hr_head, payroll_admin).
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

    # Step 2: Enforce active organization claim and subject
    org_id = claims.get("org_id")
    if not org_id:
        logger.warning(f"User {claims.get('sub')} attempted access without an active organization")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active organization selected. Please select an organization to proceed.",
        )

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

    # Step 6: Resolve User in local PostgreSQL records (permitted by users RLS under app.clerk_user_id)
    stmt_user = select(User).where((User.clerk_user_id == sub) | (User.email == sub))
    user = (await db.execute(stmt_user)).scalar_one_or_none()
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

    return AuthenticatedUserContext(
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
