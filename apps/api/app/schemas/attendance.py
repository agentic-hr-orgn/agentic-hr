"""
Pydantic Schemas for Stage 6 Attendance Entry, Daily Grid, Two-Phase Excel Import,
Auto-Fill, Month Review & Lock, and Post-Lock Edit Governance.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

AttendanceStatusLiteral = Literal[
    "PRESENT",
    "ABSENT",
    "HALF_DAY",
    "ON_LEAVE",
    "WFH",
    "HOLIDAY",
    "WEEKEND",
]

AttendanceSourceLiteral = Literal[
    "check_in",
    "manager_grid",
    "excel_import",
    "auto_fill",
    "leave_approval",
    "post_lock_edit",
]


class AttendanceRecordResponse(BaseModel):
    """Serialized attendance record for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    employee_id: UUID
    employee_name: str
    employee_email: str
    department_name: str | None = None
    attendance_date: date
    status: AttendanceStatusLiteral
    source: AttendanceSourceLiteral
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    notes: str | None = None
    leave_request_id: UUID | None = None
    locked: bool
    updated_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AttendanceTodayResponse(BaseModel):
    """Caller's today Asia/Karachi check-in status and tenant toggle state."""

    today_date: date
    year_month: str
    check_in_enabled: bool
    is_locked: bool
    record: AttendanceRecordResponse | None = None


class AttendanceCheckInRequest(BaseModel):
    """Self check-in / check-out payload."""

    action: Literal["check_in", "check_out"] | None = None
    check_out: bool = False
    notes: str | None = Field(default=None, max_length=500)
    target_date: date | None = Field(
        default=None,
        description="Optional Asia/Karachi date override (defaults to today PKT).",
    )


class DailyAttendanceGridRow(BaseModel):
    """Single employee row in the manager/HR daily attendance grid."""

    employee_id: UUID
    employee_code: str | None = None
    employee_name: str
    employee_email: str
    department_name: str | None = None
    job_title: str | None = None
    attendance_id: UUID | None = None
    attendance_date: date
    status: AttendanceStatusLiteral | None = None
    source: AttendanceSourceLiteral | None = None
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    notes: str | None = None
    locked: bool = False


class DailyAttendanceGridResponse(BaseModel):
    """Manager/HR daily attendance grid for a target date."""

    target_date: date
    year_month: str
    is_locked: bool
    total_employees: int
    marked_count: int
    unmarked_count: int
    rows: list[DailyAttendanceGridRow]


class DailyAttendanceUpsertItem(BaseModel):
    """Single employee entry in a manager daily grid batch upsert."""

    employee_id: UUID
    status: AttendanceStatusLiteral
    notes: str | None = Field(default=None, max_length=500)


class DailyAttendanceUpsertRequest(BaseModel):
    """Batch upsert payload for manager/HR daily attendance grid."""

    target_date: date
    entries: list[DailyAttendanceUpsertItem] = Field(..., min_length=1, max_length=500)


class DailyAttendanceUpsertResponse(BaseModel):
    """Summary returned after batch upserting daily attendance grid entries."""

    target_date: date
    year_month: str
    upserted_count: int
    records: list[AttendanceRecordResponse]


class AttendanceImportRowError(BaseModel):
    """Row-level error reported during Excel attendance import validation."""

    row_number: int
    field: str
    message: str


class AttendanceImportPreviewRow(BaseModel):
    """Preview row returned by two-phase attendance Excel validation."""

    row_number: int
    employee_email: str
    attendance_date: str
    status: str
    notes: str | None = None
    is_valid: bool
    errors: list[str] = Field(default_factory=list)


class AttendanceImportValidationResult(BaseModel):
    """Response from POST /api/v1/attendance/import/validate."""

    import_batch_id: str
    file_name: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    can_commit: bool
    preview_rows: list[AttendanceImportPreviewRow]
    errors: list[AttendanceImportRowError]


class AttendanceImportCommitResponse(BaseModel):
    """Response from POST /api/v1/attendance/import/{batch_id}/commit."""

    status: Literal["committed"]
    import_batch_id: str
    committed_count: int
    sheets_queued: int
    year_months: list[str]


class AttendanceAutoFillRequest(BaseModel):
    """Optional configuration for monthly attendance auto-fill."""

    overwrite_existing: bool = False


class AttendanceAutoFillResponse(BaseModel):
    """Summary of automated weekend, holiday, and approved-leave attendance fills."""

    year_month: str
    active_employees: int
    filled_leave_days: int
    filled_holiday_days: int
    filled_weekend_days: int
    total_filled: int
    skipped_existing: int


class EmployeeMonthGapItem(BaseModel):
    """Per-employee monthly summary and exact missing attendance dates."""

    employee_id: UUID
    employee_code: str | None = None
    employee_name: str
    employee_email: str
    department_name: str | None = None
    present_days: int = 0
    wfh_days: int = 0
    half_days: int = 0
    on_leave_days: int = 0
    absent_days: int = 0
    holiday_days: int = 0
    weekend_days: int = 0
    recorded_days: int = 0
    missing_days: list[date] = Field(default_factory=list)
    missing_count: int = 0


class AttendanceMonthReviewResponse(BaseModel):
    """Full-month attendance completeness review prior to locking."""

    year_month: str
    month_start: date
    month_end: date
    days_in_month: int
    active_employees: int
    total_gaps: int
    auto_fillable_gaps: int
    is_locked: bool
    can_lock: bool
    locked_at: datetime | None = None
    locked_by: UUID | None = None
    employees: list[EmployeeMonthGapItem]


class AttendanceMonthLockResponse(BaseModel):
    """Persisted month-lock summary returned by POST /api/v1/attendance/months/{year_month}/lock."""

    id: UUID
    tenant_id: UUID
    year_month: str
    locked_by: UUID | None = None
    locked_at: datetime
    employee_count: int
    record_count: int


class AttendancePostLockEditRequest(BaseModel):
    """Request to initiate a Stage 2 governance approval for editing a locked month."""

    employee_id: UUID
    attendance_date: date
    new_status: AttendanceStatusLiteral
    notes: str | None = Field(default=None, max_length=500)
    reason: str = Field(..., min_length=3, max_length=1000)
    approver_role: Literal["owner", "hr_head"] = "hr_head"

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 3:
            raise ValueError("Reason must be at least 3 characters.")
        return cleaned


class AttendancePostLockEditResponse(BaseModel):
    """Response after creating a Stage 2 `attendance.post_lock_edit` approval."""

    approval_id: UUID
    approval_status: str
    approver_role: str
    employee_id: UUID
    employee_name: str
    attendance_date: date
    year_month: str
    previous_status: AttendanceStatusLiteral | None = None
    new_status: AttendanceStatusLiteral
    notes: str | None = None
    reason: str
