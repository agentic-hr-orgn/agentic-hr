"""
Pydantic DTOs for Tenant Administration and Kill-Switch Management.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class TenantKillSwitchRequest(BaseModel):
    """Payload to engage or disengage the tenant kill-switch."""

    is_suspended: bool = Field(
        ...,
        description="Desired kill-switch state (True = suspended, False = active)",
    )
    reason: str | None = Field(
        None,
        description="Administrative rationale for tenant suspension or restoration",
        max_length=1000,
    )


class TenantKillSwitchResponse(BaseModel):
    """Authoritative response confirming updated tenant kill-switch state."""

    tenant_id: str
    tenant_name: str
    is_suspended: bool
    status: str
    suspended_at: datetime | None = None
    suspended_by: str | None = None
    suspension_reason: str | None = None
    updated_at: datetime
