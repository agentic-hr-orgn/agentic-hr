from fastapi import Header
from pydantic import BaseModel


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
    In Stage 1 skeleton, allows X-Tenant-ID header, defaulting to synthetic
    'acme-tech-01' during local development and testing.
    """
    tenant_id = x_tenant_id or "acme-tech-01"

    # In future stages, verify Clerk JWT via authorization header
    user_id = "user_synthetic_admin"
    role = "owner"

    return TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
    )
