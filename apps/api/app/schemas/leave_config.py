"""
Pydantic v2 Schemas for Stage 6 Leave & Work-Week Configuration APIs.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

VALID_WEEKDAYS: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_WEEKDAY_CANONICAL: dict[str, str] = {day.lower(): day for day in VALID_WEEKDAYS}


class WorkWeekResponse(BaseModel):
    """Serialized per-tenant work-week, probation, team coverage, and check-in settings."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    weekend_days: list[str]
    probation_months: int
    max_team_absent_pct: Decimal
    min_team_present: int
    backup_approver_user_id: UUID | None = None
    check_in_enabled: bool
    timezone: str
    created_at: datetime
    updated_at: datetime


class WorkWeekUpdateRequest(BaseModel):
    """Payload accepted by PUT /api/v1/leave/config/work-week."""

    model_config = ConfigDict(extra="forbid")

    weekend_days: list[str] | None = Field(
        default=None,
        description="Weekend day names (e.g. ['Saturday', 'Sunday'])",
    )
    probation_months: int | None = Field(default=None, ge=0, le=24)
    max_team_absent_pct: Decimal | None = Field(
        default=None,
        ge=Decimal("0.00"),
        le=Decimal("100.00"),
    )
    min_team_present: int | None = Field(default=None, ge=0, le=1000)
    backup_approver_user_id: UUID | None = None
    check_in_enabled: bool | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("weekend_days")
    @classmethod
    def validate_weekend_days(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            key = str(raw).strip().lower()
            canonical = _WEEKDAY_CANONICAL.get(key)
            if not canonical:
                raise ValueError(
                    f"Invalid weekday '{raw}'. Allowed values: {', '.join(VALID_WEEKDAYS)}."
                )
            if canonical not in seen:
                seen.add(canonical)
                normalized.append(canonical)
        if len(normalized) >= 7:
            raise ValueError("weekend_days cannot include all 7 days of the week.")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("timezone cannot be empty.")
        return clean


class LeaveTypeResponse(BaseModel):
    """Serialized leave type configuration."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    code: str
    name: str
    annual_allowance_days: Decimal
    accrual_mode: Literal["upfront", "monthly"]
    carry_forward_cap_days: Decimal
    max_consecutive_days: int | None = None
    min_notice_days: int
    allow_during_probation: bool
    requires_document_after_days: int | None = None
    allow_half_day: bool
    is_paid: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LeaveTypeCreateRequest(BaseModel):
    """Payload accepted by POST /api/v1/leave/config/types."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    annual_allowance_days: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0.00"),
        le=Decimal("365.00"),
    )
    accrual_mode: Literal["upfront", "monthly"] = "upfront"
    carry_forward_cap_days: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0.00"),
        le=Decimal("365.00"),
    )
    max_consecutive_days: int | None = Field(default=None, ge=1, le=365)
    min_notice_days: int = Field(default=0, ge=0, le=365)
    allow_during_probation: bool = False
    requires_document_after_days: int | None = Field(default=None, ge=1, le=365)
    allow_half_day: bool = True
    is_paid: bool = True
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        clean = value.strip().lower()
        if not clean:
            raise ValueError("code must be a non-empty string.")
        return clean

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("name must be a non-empty string.")
        return clean


class LeaveTypeUpdateRequest(BaseModel):
    """Payload accepted by PATCH /api/v1/leave/config/types/{leave_type_id}."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    annual_allowance_days: Decimal | None = Field(
        default=None,
        ge=Decimal("0.00"),
        le=Decimal("365.00"),
    )
    accrual_mode: Literal["upfront", "monthly"] | None = None
    carry_forward_cap_days: Decimal | None = Field(
        default=None,
        ge=Decimal("0.00"),
        le=Decimal("365.00"),
    )
    max_consecutive_days: int | None = Field(default=None, ge=1, le=365)
    min_notice_days: int | None = Field(default=None, ge=0, le=365)
    allow_during_probation: bool | None = None
    requires_document_after_days: int | None = Field(default=None, ge=1, le=365)
    allow_half_day: bool | None = None
    is_paid: bool | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("name cannot be empty.")
        return clean


class LeaveTypeDeleteResponse(BaseModel):
    """Response returned by DELETE /api/v1/leave/config/types/{leave_type_id}."""

    id: UUID
    code: str
    action: Literal["deleted", "deactivated"]
    is_active: bool


class HolidayResponse(BaseModel):
    """Serialized tenant holiday record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    holiday_date: date
    name: str
    is_optional: bool
    source: Literal["default_pk", "custom"]
    created_at: datetime
    updated_at: datetime


class HolidayCreateRequest(BaseModel):
    """Payload accepted by POST /api/v1/leave/config/holidays."""

    model_config = ConfigDict(extra="forbid")

    holiday_date: date
    name: str = Field(..., min_length=1, max_length=200)
    is_optional: bool = False
    source: Literal["default_pk", "custom"] = "custom"

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("name must be a non-empty string.")
        return clean


class HolidayUpdateRequest(BaseModel):
    """Payload accepted by PATCH /api/v1/leave/config/holidays/{holiday_id}."""

    model_config = ConfigDict(extra="forbid")

    holiday_date: date | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_optional: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("name cannot be empty.")
        return clean


class HolidayDeleteResponse(BaseModel):
    """Response returned by DELETE /api/v1/leave/config/holidays/{holiday_id}."""

    id: UUID
    holiday_date: date
    action: Literal["deleted"] = "deleted"
