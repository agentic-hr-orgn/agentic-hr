"""
Pydantic v2 Schemas for Stage 6 Leave Intake, Submission, Decisions, Balances & Calendar APIs.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.leave_policy_engine import RuleOutcome


class LeaveIntakeExtraction(BaseModel):
    """Structured extraction returned by ModelGateway / FakeModel for leave intake chat."""

    leave_type_code: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_half_day: bool = False
    half_day_period: Literal["morning", "afternoon"] | None = None
    reason: str | None = None
    clarifying_question: str | None = None


class LeaveIntakeRequest(BaseModel):
    """Payload accepted by POST /api/v1/leave/intake."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=2000)
    conversation_context: dict[str, Any] | None = None
    employee_id: UUID | None = None


class LeaveConfirmCard(BaseModel):
    """Pre-submission confirmation card produced once intake slots are complete."""

    employee_id: UUID
    employee_name: str
    leave_type_id: UUID
    leave_type_code: str
    leave_type_name: str
    start_date: date
    end_date: date
    is_half_day: bool = False
    half_day_period: Literal["morning", "afternoon"] | None = None
    working_days: Decimal
    reason: str
    document_id: UUID | None = None
    balance_before: Decimal
    balance_after: Decimal
    policy_check_status: Literal["OK", "VIOLATION", "BLOCKED"]
    policy_check_reasons: list[RuleOutcome] = Field(default_factory=list)


class LeaveIntakeResponse(BaseModel):
    """Response returned by POST /api/v1/leave/intake."""

    status: Literal["needs_clarification", "ready_to_confirm"]
    clarifying_question: str | None = None
    extracted: dict[str, Any] = Field(default_factory=dict)
    confirm_card: LeaveConfirmCard | None = None


class LeaveRequestCreateRequest(BaseModel):
    """Payload accepted by POST /api/v1/leave/requests."""

    model_config = ConfigDict(extra="forbid")

    leave_type_id: UUID | None = None
    leave_type_code: str | None = Field(default=None, min_length=1, max_length=64)
    start_date: date
    end_date: date
    is_half_day: bool = False
    half_day_period: Literal["morning", "afternoon"] | None = None
    reason: str = Field(..., min_length=2, max_length=1000)
    document_id: UUID | None = None
    employee_id: UUID | None = None
    as_of_date: date | None = None
    start_workflow: bool = True
    reminder_hours: float | None = Field(default=None, gt=0)
    escalation_hours: float | None = Field(default=None, gt=0)
    expiry_hours: float | None = Field(default=None, gt=0)

    @field_validator("leave_type_code")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip().lower()
        return clean or None

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 2:
            raise ValueError("reason must be at least 2 non-whitespace characters.")
        return clean


class LeaveDecisionRequest(BaseModel):
    """Payload accepted by POST /api/v1/leave/requests/{request_id}/decide."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=1000)


class LeaveCancelRequest(BaseModel):
    """Payload accepted by POST /api/v1/leave/requests/{request_id}/cancel."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class LeaveRequestResponse(BaseModel):
    """Serialized leave request DTO with joined employee & leave type metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    employee_id: UUID
    employee_name: str
    employee_email: str | None = None
    department_name: str | None = None
    leave_type_id: UUID
    leave_type_code: str
    leave_type_name: str
    start_date: date
    end_date: date
    is_half_day: bool
    half_day_period: str | None = None
    working_days: Decimal
    reason: str
    document_id: UUID | None = None
    policy_check_status: str
    policy_check_reasons: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    approver_user_id: UUID | None = None
    escalated_to_user_id: UUID | None = None
    hr_exception_approval_id: UUID | None = None
    hr_exception_status: str | None = None
    workflow_id: str | None = None
    calendar_event_id: str | None = None
    sheets_row_synced: bool = False
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    created_at: datetime
    updated_at: datetime


class LeaveBalanceItemResponse(BaseModel):
    """Serialized leave balance row with computed available_days."""

    id: UUID
    tenant_id: UUID
    employee_id: UUID
    employee_name: str
    leave_type_id: UUID
    leave_type_code: str
    leave_type_name: str
    year: int
    allocated_days: Decimal
    carried_forward_days: Decimal
    reserved_days: Decimal
    used_days: Decimal
    available_days: Decimal
    is_paid: bool
    accrual_mode: str


class TeamCalendarItemResponse(BaseModel):
    """Team calendar entry for approved/pending leaves."""

    request_id: UUID
    employee_id: UUID
    employee_name: str
    department_name: str | None = None
    leave_type_code: str
    leave_type_name: str
    start_date: date
    end_date: date
    is_half_day: bool
    half_day_period: str | None = None
    working_days: Decimal
    status: str


class LeaveOverviewResponse(BaseModel):
    """HR overview counters and list of tenant leave requests."""

    total_requests: int
    pending_count: int
    pending_hr_exception_count: int
    approved_count: int
    rejected_count: int
    cancelled_count: int
    expired_count: int
    requests: list[LeaveRequestResponse] = Field(default_factory=list)
