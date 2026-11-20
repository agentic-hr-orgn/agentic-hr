"""
Pydantic Schemas for Stage 4 Onboarding Wizard State, Readiness & Tenant Launch.
Normalizes launch timestamps to Asia/Karachi (PKT, UTC+05:00).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.core.timezones import iso_pkt

DEFAULT_WEEKENDS: list[str] = ["Saturday", "Sunday"]
DEFAULT_LEAVE_RULES: dict[str, Any] = {
    "annual_days": 20,
    "sick_days": 10,
    "casual_days": 10,
    "carry_forward_max": 5,
}


class OnboardingStepSaveRequest(BaseModel):
    """Payload accepted by PUT /api/v1/onboarding/steps/{step}."""

    business_type: str | None = Field(default=None, max_length=128)
    structure_config: dict[str, Any] | None = Field(default=None)
    advance_step: bool = Field(default=True)
    current_step: int | None = Field(default=None, ge=1, le=5)


class OnboardingStateResponse(BaseModel):
    """Serialized onboarding wizard state returned by GET /state and PUT /steps/{step}."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    current_step: int
    completed_steps: list[int]
    business_type: str | None = None
    structure_config: dict[str, Any]
    is_live: bool
    launched_at: datetime | None = None
    updated_by: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("launched_at")
    def _serialize_launched_at(self, value: datetime | None) -> str | None:
        return iso_pkt(value)


class OnboardingReadinessCounts(BaseModel):
    """Entity counts backing the Step 5 launch readiness checklist."""

    connectors: int
    policies: int
    departments: int
    employees: int


class OnboardingReadinessResponse(BaseModel):
    """Deterministic Step 5 readiness checklist response."""

    connectors_ok: bool
    policies_ok: bool
    departments_ok: bool
    employees_ok: bool
    can_go_live: bool
    counts: OnboardingReadinessCounts
    missing_items: list[str]


class OnboardingLaunchResponse(BaseModel):
    """Response payload returned by POST /api/v1/onboarding/launch."""

    status: str
    is_live: bool
    launched_at: str
    invitations_sent: int
    invitations_skipped: int
    sheets_queued: int
    readiness: OnboardingReadinessResponse
