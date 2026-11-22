"""
Pure Deterministic Leave Policy Check Engine for Agentic HR Core (Stage 6 Prompt 2).

Complies with Rule 3 (LLM proposes, code decides) and ADR 009:
- 100% pure function: ZERO database, network, or system clock (`datetime.now()` / `date.today()`)
  side effects. All inputs (including `as_of_date`) are passed explicitly.
- Evaluates all 9 leave rules in a single pass and returns structured `RuleOutcome` diagnostics
  with precedence `BLOCKED > VIOLATION > OK`.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

WEEKDAY_TO_INDEX: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

ACTIVE_OVERLAP_STATUSES: frozenset[str] = frozenset({"pending", "pending_hr_exception", "approved"})


class LeaveCheckInput(BaseModel):
    """Pure input describing a leave request and its applicable leave-type / work-week rules."""

    model_config = ConfigDict(frozen=True)

    leave_type_code: str = Field(..., min_length=1, max_length=64)
    start_date: date
    end_date: date
    as_of_date: date
    is_half_day: bool = False
    half_day_period: Literal["morning", "afternoon"] | None = None
    document_id: UUID | str | None = None
    weekend_days: tuple[str, ...] = ("Saturday", "Sunday")
    probation_months: int = Field(default=3, ge=0, le=24)
    allow_half_day: bool = True
    allow_during_probation: bool = False
    max_consecutive_days: int | None = Field(default=None, ge=1)
    min_notice_days: int = Field(default=0, ge=0)
    requires_document_after_days: int | None = Field(default=None, ge=1)
    is_paid: bool = True


class EmployeeCheckState(BaseModel):
    """Minimal employee state required by the pure leave policy check engine."""

    model_config = ConfigDict(frozen=True)

    employee_id: UUID | str
    hire_date: date | None = None
    status: str = "active"


class BalanceSnapshot(BaseModel):
    """Point-in-time snapshot of an employee's leave balance for a leave type and year."""

    model_config = ConfigDict(frozen=True)

    leave_type_code: str
    year: int
    allocated_days: Decimal = Decimal("0.00")
    carried_forward_days: Decimal = Decimal("0.00")
    reserved_days: Decimal = Decimal("0.00")
    used_days: Decimal = Decimal("0.00")

    @property
    def available_days(self) -> Decimal:
        """Deterministic balance formula (ADR 009)."""
        return (
            Decimal(self.allocated_days)
            + Decimal(self.carried_forward_days)
            - Decimal(self.reserved_days)
            - Decimal(self.used_days)
        ).quantize(Decimal("0.01"))


class ExistingLeaveSpan(BaseModel):
    """Existing leave request span used for deterministic overlap detection."""

    model_config = ConfigDict(frozen=True)

    start_date: date
    end_date: date
    status: str
    is_half_day: bool = False
    half_day_period: Literal["morning", "afternoon"] | None = None
    request_id: UUID | str | None = None


class TeamCoverageState(BaseModel):
    """Department/team coverage snapshot for evaluating team-absent thresholds."""

    model_config = ConfigDict(frozen=True)

    team_size: int = Field(..., ge=0)
    absent_by_date: dict[date, int] = Field(default_factory=dict)
    max_team_absent_pct: Decimal = Field(
        default=Decimal("30.00"), ge=Decimal("0.00"), le=Decimal("100.00")
    )
    min_team_present: int = Field(default=1, ge=0)


class RuleOutcome(BaseModel):
    """Structured diagnostic outcome for a single policy rule evaluation."""

    model_config = ConfigDict(frozen=True)

    rule: str
    status: Literal["OK", "VIOLATION", "BLOCKED"]
    code: str
    message: str


class PolicyCheckResult(BaseModel):
    """Aggregated deterministic output of `check_leave`."""

    model_config = ConfigDict(frozen=True)

    status: Literal["OK", "VIOLATION", "BLOCKED"]
    working_days: Decimal
    reasons: list[RuleOutcome] = Field(default_factory=list)


def add_calendar_months(base_date: date, months: int) -> date:
    """
    Adds `months` calendar months to `base_date`, clamping day-of-month to the target
    month's valid maximum (e.g., Jan 31 + 1 month -> Feb 28/29). Pure & deterministic.
    """
    if months == 0:
        return base_date
    total_months = (base_date.year * 12 + (base_date.month - 1)) + months
    target_year, zero_based_month = divmod(total_months, 12)
    target_month = zero_based_month + 1
    max_day = calendar.monthrange(target_year, target_month)[1]
    return date(target_year, target_month, min(base_date.day, max_day))


def compute_working_dates(
    start_date: date,
    end_date: date,
    weekend_days: Iterable[str] = ("Saturday", "Sunday"),
    holidays: Iterable[date] = (),
) -> list[date]:
    """
    Returns ordered working dates in `[start_date, end_date]` excluding configured
    `weekend_days` and `holidays`. Returns `[]` if `end_date < start_date`.
    """
    if end_date < start_date:
        return []
    weekend_indices = {
        WEEKDAY_TO_INDEX[str(day).strip().lower()]
        for day in weekend_days
        if str(day).strip().lower() in WEEKDAY_TO_INDEX
    }
    holiday_set = set(holidays)

    working_dates: list[date] = []
    curr = start_date
    one_day = timedelta(days=1)
    while curr <= end_date:
        if curr.weekday() not in weekend_indices and curr not in holiday_set:
            working_dates.append(curr)
        curr += one_day
    return working_dates


def _resolve_balance(
    balances: dict[str, BalanceSnapshot] | Sequence[BalanceSnapshot] | BalanceSnapshot | None,
    leave_type_code: str,
    target_year: int,
) -> BalanceSnapshot | None:
    if balances is None:
        return None
    norm_code = leave_type_code.strip().lower()
    if isinstance(balances, BalanceSnapshot):
        return balances if balances.leave_type_code.strip().lower() == norm_code else None
    if isinstance(balances, dict):
        if norm_code in balances:
            return balances[norm_code]
        for key, snap in balances.items():
            if key.strip().lower() == norm_code:
                return snap
        return None

    fallback: BalanceSnapshot | None = None
    for snap in balances:
        if snap.leave_type_code.strip().lower() == norm_code:
            if snap.year == target_year:
                return snap
            if fallback is None:
                fallback = snap
    return fallback


def _has_overlap(request: LeaveCheckInput, existing: ExistingLeaveSpan) -> bool:
    if existing.status.strip().lower() not in ACTIVE_OVERLAP_STATUSES:
        return False
    if existing.end_date < request.start_date or existing.start_date > request.end_date:
        return False

    # Half-day vs. half-day on the same date: distinct periods (morning vs. afternoon) do not clash
    return not (
        request.is_half_day
        and existing.is_half_day
        and request.start_date == request.end_date == existing.start_date == existing.end_date
        and request.half_day_period is not None
        and existing.half_day_period is not None
        and request.half_day_period != existing.half_day_period
    )


def check_leave(
    request: LeaveCheckInput,
    employee: EmployeeCheckState,
    balances: dict[str, BalanceSnapshot] | Sequence[BalanceSnapshot] | BalanceSnapshot | None,
    holidays: Iterable[date],
    existing_leaves: Sequence[ExistingLeaveSpan],
    team_state: TeamCoverageState | None = None,
) -> PolicyCheckResult:
    """
    Pure deterministic leave policy check engine (9 rules).

    Rules evaluated:
    1. Working days & half-day validation (`INVALID_DATE_RANGE`, `HALF_DAY_NOT_ALLOWED`, `INVALID_HALF_DAY`)
    2. Weekend/Holiday-only range (`NO_WORKING_DAYS` -> BLOCKED)
    3. Overlap check with pending/pending_hr_exception/approved leaves (`OVERLAPPING_LEAVE` -> BLOCKED)
    4. Balance availability check (`INSUFFICIENT_BALANCE` -> BLOCKED)
    5. Probation check (`DURING_PROBATION` -> VIOLATION)
    6. Max consecutive working days (`EXCEEDS_MAX_CONSECUTIVE_DAYS` -> VIOLATION)
    7. Notice period check (`SHORT_NOTICE` -> VIOLATION)
    8. Document requirement (`DOCUMENT_REQUIRED` -> BLOCKED when `working_days > requires_document_after_days`)
    9. Team coverage threshold (`TEAM_COVERAGE_EXCEEDED` -> VIOLATION)
    """
    outcomes: list[RuleOutcome] = []

    # Rule 1: Date order & working days calculation
    if request.end_date < request.start_date:
        outcomes.append(
            RuleOutcome(
                rule="working_days",
                status="BLOCKED",
                code="INVALID_DATE_RANGE",
                message=(
                    f"End date ({request.end_date.isoformat()}) cannot be before "
                    f"start date ({request.start_date.isoformat()})."
                ),
            )
        )
        return PolicyCheckResult(
            status="BLOCKED",
            working_days=Decimal("0.00"),
            reasons=outcomes,
        )

    working_dates = compute_working_dates(
        start_date=request.start_date,
        end_date=request.end_date,
        weekend_days=request.weekend_days,
        holidays=holidays,
    )

    if request.is_half_day:
        if not request.allow_half_day:
            outcomes.append(
                RuleOutcome(
                    rule="working_days",
                    status="BLOCKED",
                    code="HALF_DAY_NOT_ALLOWED",
                    message=f"Leave type '{request.leave_type_code}' does not permit half-day requests.",
                )
            )
        if request.start_date != request.end_date or request.half_day_period not in (
            "morning",
            "afternoon",
        ):
            outcomes.append(
                RuleOutcome(
                    rule="working_days",
                    status="BLOCKED",
                    code="INVALID_HALF_DAY",
                    message="Half-day leave must span a single date and specify 'morning' or 'afternoon'.",
                )
            )
        working_days = Decimal("0.50") if working_dates else Decimal("0.00")
    else:
        working_days = Decimal(len(working_dates)).quantize(Decimal("0.01"))

    # Rule 2: Weekend/Holiday-only range
    if working_days == Decimal("0.00"):
        outcomes.append(
            RuleOutcome(
                rule="working_days",
                status="BLOCKED",
                code="NO_WORKING_DAYS",
                message="Selected date range contains zero working days (weekends/holidays only).",
            )
        )

    # Rule 3: Overlap check
    overlapping = [span for span in existing_leaves if _has_overlap(request, span)]
    if overlapping:
        first = overlapping[0]
        outcomes.append(
            RuleOutcome(
                rule="overlap",
                status="BLOCKED",
                code="OVERLAPPING_LEAVE",
                message=(
                    f"Request overlaps with an existing {first.status} leave "
                    f"({first.start_date.isoformat()} to {first.end_date.isoformat()})."
                ),
            )
        )

    # Rule 4: Balance check
    balance = _resolve_balance(balances, request.leave_type_code, request.start_date.year)
    available = balance.available_days if balance is not None else Decimal("0.00")
    has_explicit_unpaid_cap = balance is not None and (
        Decimal(balance.allocated_days) + Decimal(balance.carried_forward_days)
    ) > Decimal("0.00")
    if (request.is_paid or has_explicit_unpaid_cap) and working_days > available:
        outcomes.append(
            RuleOutcome(
                rule="balance",
                status="BLOCKED",
                code="INSUFFICIENT_BALANCE",
                message=(
                    f"Requested {working_days} working day(s) exceeds available "
                    f"{request.leave_type_code} balance ({available} day(s))."
                ),
            )
        )

    # Rule 5: Probation check (start_date < hire_date + probation_months)
    if (
        employee.hire_date is not None
        and request.probation_months > 0
        and not request.allow_during_probation
    ):
        probation_end = add_calendar_months(employee.hire_date, request.probation_months)
        if request.start_date < probation_end:
            outcomes.append(
                RuleOutcome(
                    rule="probation",
                    status="VIOLATION",
                    code="DURING_PROBATION",
                    message=(
                        f"Employee is in probation until {probation_end.isoformat()}; "
                        f"'{request.leave_type_code}' requires HR exception during probation."
                    ),
                )
            )

    # Rule 6: Max consecutive days
    if request.max_consecutive_days is not None and working_days > Decimal(
        request.max_consecutive_days
    ):
        outcomes.append(
            RuleOutcome(
                rule="max_consecutive_days",
                status="VIOLATION",
                code="EXCEEDS_MAX_CONSECUTIVE_DAYS",
                message=(
                    f"Requested {working_days} working day(s) exceeds maximum consecutive limit "
                    f"of {request.max_consecutive_days} day(s) for '{request.leave_type_code}'."
                ),
            )
        )

    # Rule 7: Notice period
    if request.min_notice_days > 0:
        notice_days = (request.start_date - request.as_of_date).days
        if notice_days < request.min_notice_days:
            outcomes.append(
                RuleOutcome(
                    rule="notice_period",
                    status="VIOLATION",
                    code="SHORT_NOTICE",
                    message=(
                        f"Request provides {notice_days} day(s) notice, which is below the "
                        f"minimum {request.min_notice_days} day(s) required for '{request.leave_type_code}'."
                    ),
                )
            )

    # Rule 8: Document requirement (strict `>` threshold: e.g. >2 days => 3+ days requires document)
    if (
        request.requires_document_after_days is not None
        and working_days > Decimal(request.requires_document_after_days)
        and request.document_id is None
    ):
        outcomes.append(
            RuleOutcome(
                rule="document_required",
                status="BLOCKED",
                code="DOCUMENT_REQUIRED",
                message=(
                    f"Supporting document is required when '{request.leave_type_code}' "
                    f"exceeds {request.requires_document_after_days} working day(s)."
                ),
            )
        )

    # Rule 9: Team coverage threshold (guard team_size > 0 to avoid division by zero)
    if team_state is not None and team_state.team_size > 0 and working_dates:
        violated_dates: list[str] = []
        worst_pct = Decimal("0.00")
        worst_present = team_state.team_size
        for d in working_dates:
            existing_absent = max(0, int(team_state.absent_by_date.get(d, 0)))
            projected_absent = existing_absent + 1
            projected_present = max(0, team_state.team_size - projected_absent)
            absent_pct = (
                (Decimal(projected_absent) * Decimal(100)) / Decimal(team_state.team_size)
            ).quantize(Decimal("0.01"))
            if (
                absent_pct > team_state.max_team_absent_pct
                or projected_present < team_state.min_team_present
            ):
                violated_dates.append(d.isoformat())
                worst_pct = max(worst_pct, absent_pct)
                worst_present = min(worst_present, projected_present)

        if violated_dates:
            outcomes.append(
                RuleOutcome(
                    rule="team_coverage",
                    status="VIOLATION",
                    code="TEAM_COVERAGE_EXCEEDED",
                    message=(
                        f"Team coverage threshold exceeded on {', '.join(violated_dates)} "
                        f"(projected absent {worst_pct}% vs max {team_state.max_team_absent_pct}%, "
                        f"projected present {worst_present} vs min {team_state.min_team_present})."
                    ),
                )
            )

    # Status aggregation: BLOCKED > VIOLATION > OK
    overall_status: Literal["OK", "VIOLATION", "BLOCKED"] = "OK"
    if any(r.status == "BLOCKED" for r in outcomes):
        overall_status = "BLOCKED"
    elif any(r.status == "VIOLATION" for r in outcomes):
        overall_status = "VIOLATION"

    return PolicyCheckResult(
        status=overall_status,
        working_days=working_days,
        reasons=outcomes,
    )
