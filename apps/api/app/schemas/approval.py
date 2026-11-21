"""
Typed Pydantic Schemas for Human Approval Payloads and Request/Response DTOs.
Complies with Stage 2 Phase 6 specifications.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ==============================================================================
# Domain Approval Payload Schemas
# ==============================================================================
class LeaveRequestPayload(BaseModel):
    """Payload schema for employee leave approval requests."""

    model_config = ConfigDict(extra="forbid")

    leave_type: Literal["annual", "casual", "sick", "maternity", "paternity", "unpaid"] = Field(
        ..., description="Standard leave category"
    )
    start_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Leave start date (YYYY-MM-DD)",
    )
    end_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Leave end date (YYYY-MM-DD)",
    )
    days_count: float = Field(..., gt=0, le=90, description="Total duration in business days")
    reason: str = Field(
        ..., min_length=3, max_length=500, description="Explanation for leave request"
    )

    @field_validator("end_date")
    @classmethod
    def validate_date_order(cls, v: str, info) -> str:
        start = info.data.get("start_date")
        if start and v < start:
            raise ValueError("end_date must be greater than or equal to start_date")
        return v


class CandidateOfferPayload(BaseModel):
    """Payload schema for candidate job offer approvals."""

    model_config = ConfigDict(extra="forbid")

    candidate_name: str = Field(..., min_length=2, max_length=120)
    candidate_email: str = Field(
        ...,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="Candidate email address",
    )
    position: str = Field(..., min_length=2, max_length=100)
    offered_salary_pkr: int = Field(
        ..., gt=0, description="Monthly proposed salary in Pakistani Rupees (PKR)"
    )
    start_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Anticipated joining date (YYYY-MM-DD)",
    )


class PayrollOverridePayload(BaseModel):
    """Payload schema for discretionary payroll adjustments or bonuses."""

    model_config = ConfigDict(extra="forbid")

    employee_id: str = Field(..., min_length=1)
    employee_name: str = Field(..., min_length=2)
    bonus_pkr: int | None = Field(default=None, ge=0)
    deduction_pkr: int | None = Field(default=None, ge=0)
    net_adjustment_pkr: int | None = Field(
        default=None,
        description="Calculated net impact: bonus - deduction in PKR",
    )
    justification: str = Field(..., min_length=5, max_length=500)

    @model_validator(mode="after")
    def calculate_net_and_validate(self) -> "PayrollOverridePayload":
        bonus = self.bonus_pkr or 0
        deduction = self.deduction_pkr or 0
        if bonus == 0 and deduction == 0:
            raise ValueError("At least one non-zero bonus_pkr or deduction_pkr must be specified.")
        self.net_adjustment_pkr = bonus - deduction
        return self


class PolicyExceptionPayload(BaseModel):
    """Payload schema for non-standard corporate policy exception approvals."""

    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(..., min_length=3, max_length=100)
    exception_description: str = Field(..., min_length=10, max_length=1000)
    impact_assessment: str = Field(..., min_length=5, max_length=500)


class PolicyPublishPayload(BaseModel):
    """Payload schema for Stage 5 corporate policy publish governance approvals."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=255)
    version: int = Field(..., ge=1)
    visibility: Literal["all", "managers", "hr_only"]
    doc_type: Literal["company_policy", "labour_law", "helpdesk_knowledge"]
    supersedes_document_id: str | None = None
    chunk_count: int = Field(default=0, ge=0)
    has_prompt_injection_warning: bool = False


# Payload Schema Registry
APPROVAL_PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = {
    "leave_request": LeaveRequestPayload,
    "candidate_offer": CandidateOfferPayload,
    "payroll_override": PayrollOverridePayload,
    "policy_exception": PolicyExceptionPayload,
    "policy.publish": PolicyPublishPayload,
}


def validate_approval_payload(approval_type: str, raw_payload: dict[str, Any]) -> dict[str, Any]:
    """Validates raw JSON payload against the matching domain Pydantic schema."""
    schema_cls = APPROVAL_PAYLOAD_SCHEMAS.get(approval_type)
    if not schema_cls:
        allowed = ", ".join(APPROVAL_PAYLOAD_SCHEMAS.keys())
        raise ValueError(f"Unsupported approval_type '{approval_type}'. Supported types: {allowed}")

    validated_model = schema_cls.model_validate(raw_payload)
    return validated_model.model_dump(mode="json")


# ==============================================================================
# Request and Response DTOs
# ==============================================================================
class ApprovalCreateRequest(BaseModel):
    """DTO for submitting a new human approval request."""

    approval_type: Literal[
        "leave_request",
        "candidate_offer",
        "payroll_override",
        "policy_exception",
        "policy.publish",
    ] = Field(..., description="Type of approval being requested")
    payload: dict[str, Any] = Field(
        ..., description="Domain-specific payload matching the approval_type"
    )
    approver_role: Literal["owner", "manager", "hr_head", "payroll_admin"] = Field(
        ..., description="Required role or permission flag to decide this request"
    )
    workflow_id: str | None = Field(
        default=None,
        max_length=128,
        description="Optional Temporal workflow execution ID associated with this approval gate",
    )


class ApprovalDecisionRequest(BaseModel):
    """DTO for approving or rejecting an approval request."""

    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Decision reason. Mandatory for rejection; optional for approval.",
    )


class ApprovalRequestChangesRequest(BaseModel):
    """DTO for requesting changes on an approval request."""

    reason: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Mandatory correction instructions detailing what amendments are required.",
    )


class ApprovalResubmitRequest(BaseModel):
    """DTO for resubmitting a corrected approval draft."""

    payload: dict[str, Any] = Field(
        ...,
        description="Updated domain payload matching the original approval_type",
    )


class ApprovalRevisionResponse(BaseModel):
    """DTO representing an immutable historical revision round."""

    id: str
    approval_id: str
    revision_number: int
    payload: dict[str, Any]
    submitted_by: str | None = None
    submitter_name: str | None = None
    submitted_at: str
    decision: str | None = None
    decided_by: str | None = None
    decider_name: str | None = None
    decision_reason: str | None = None
    decided_at: str | None = None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class ApprovalResponse(BaseModel):
    """DTO returned for approval records."""

    id: str
    tenant_id: str
    approval_type: str
    current_revision: int = 1
    payload: dict[str, Any]
    requester_id: str | None = None
    requester_name: str | None = None
    requester_email: str | None = None
    approver_role: str
    status: str
    decided_by: str | None = None
    decider_name: str | None = None
    decision_reason: str | None = None
    created_at: str
    decided_at: str | None = None
    resubmitted_at: str | None = None
    workflow_id: str | None = None
    revisions: list[ApprovalRevisionResponse] = []

    model_config = ConfigDict(from_attributes=True)
