"""
Transactional Leave Lifecycle Service for Agentic HR Core (Stage 6 Prompt 3).

Complies with ADR 002 (Forced RLS), ADR 005 (Postgres authoritative, Sheets synced view),
ADR 009 (Double-entry `leave_ledger`, `SELECT ... FOR UPDATE`, canonical lock ordering,
single idempotent `approve_leave_request`), and Pass 2 transaction boundaries.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import AuthenticatedUserContext
from app.core.temporal import get_temporal_client
from app.core.timezones import KARACHI_TZ_NAME, get_karachi_tz, to_karachi
from app.integrations.base import GmailDeliveryError
from app.models.approval import Approval
from app.models.attendance import Attendance, AttendanceMonthLock
from app.models.connector import ConnectorCredential
from app.models.department import Department
from app.models.employee import Employee
from app.models.leave import (
    Holiday,
    LeaveBalance,
    LeaveLedger,
    LeaveRequest,
    LeaveType,
    WorkWeek,
)
from app.models.membership import Membership
from app.models.role import Role
from app.models.sheets import SheetsSyncQueue
from app.models.tenant import set_tenant_context
from app.models.user import User
from app.schemas.approval import ApprovalCreateRequest
from app.schemas.leave import (
    LeaveBalanceItemResponse,
    LeaveDecisionRequest,
    LeaveOverviewResponse,
    LeaveRequestCreateRequest,
    LeaveRequestResponse,
    TeamCalendarItemResponse,
)
from app.services.audit_service import write_audit
from app.services.calendar_service import CalendarSchedulingService
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    CredentialNotFoundError,
    ReadOnlyConnectorError,
)
from app.services.email_delivery_service import EmailDeliveryService, RecipientBlockedError
from app.services.leave_config_service import (
    LeaveConfigService,
    initialize_employee_leave_balances,
)
from app.services.leave_policy_engine import (
    ACTIVE_OVERLAP_STATUSES,
    BalanceSnapshot,
    EmployeeCheckState,
    ExistingLeaveSpan,
    LeaveCheckInput,
    PolicyCheckResult,
    TeamCoverageState,
    check_leave,
    compute_working_dates,
)
from app.services.notification_service import NotificationService
from app.services.outbox_service import enqueue_outbox_event
from app.services.sheets_sync_service import SheetsSyncService

logger = logging.getLogger("agentic_hr.services.leave")


def _is_hr_or_owner(actor: AuthenticatedUserContext) -> bool:
    return bool(actor.role == "owner" or actor.is_hr_head)


def _format_date_range(start_d: date, end_d: date) -> str:
    if start_d == end_d:
        return start_d.isoformat()
    return f"{start_d.isoformat()} to {end_d.isoformat()}"


def _year_months_in_range(start_d: date, end_d: date) -> list[str]:
    months: list[str] = []
    curr_year, curr_month = start_d.year, start_d.month
    while (curr_year, curr_month) <= (end_d.year, end_d.month):
        months.append(f"{curr_year:04d}-{curr_month:02d}")
        if curr_month == 12:
            curr_year += 1
            curr_month = 1
        else:
            curr_month += 1
    return months


async def resolve_target_employee(
    db: AsyncSession,
    actor: AuthenticatedUserContext,
    employee_id: UUID | None = None,
) -> Employee:
    """
    Resolves the target `Employee` row for `actor`. If `employee_id` is supplied for a
    different employee, requires `owner` or `is_hr_head` privileges.
    """
    t_id = UUID(str(actor.tenant_id))
    actor_user_uuid = UUID(str(actor.user_id))

    if employee_id is not None:
        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == t_id,
                    Employee.id == employee_id,
                )
            )
        ).scalar_one_or_none()
        if emp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee '{employee_id}' not found.",
            )
        is_self = emp.user_id == actor_user_uuid or emp.email.lower() == actor.email.lower()
        if not is_self and not _is_hr_or_owner(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Owner or HR Head may submit leave requests on behalf of another employee.",
            )
        return emp

    emp = (
        await db.execute(
            select(Employee).where(
                Employee.tenant_id == t_id,
                Employee.user_id == actor_user_uuid,
            )
        )
    ).scalar_one_or_none()
    if emp is None:
        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == t_id,
                    func.lower(Employee.email) == actor.email.strip().lower(),
                )
            )
        ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No employee profile is linked to your user account in this tenant.",
        )
    return emp


async def _resolve_approver_user_id(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    employee: Employee,
    work_week: WorkWeek,
) -> UUID | None:
    """Resolves primary approver: employee's manager's user_id -> backup_approver -> HR Head/Owner."""
    if employee.manager_id is not None:
        mgr_user_id = (
            await db.execute(
                select(Employee.user_id).where(
                    Employee.tenant_id == tenant_id,
                    Employee.id == employee.manager_id,
                )
            )
        ).scalar_one_or_none()
        if mgr_user_id is not None:
            return mgr_user_id

    if work_week.backup_approver_user_id is not None:
        return work_week.backup_approver_user_id

    hr_or_owner = (
        await db.execute(
            select(Membership.user_id)
            .join(Role, Membership.role_id == Role.id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.status == "active",
                or_(Membership.is_hr_head.is_(True), Role.role_key == "owner"),
            )
            .order_by(Membership.is_hr_head.desc(), Membership.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return hr_or_owner


async def _ensure_and_lock_balance(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    employee_id: UUID,
    leave_type_id: UUID,
    year: int,
    actor_id: UUID | str | None = None,
) -> LeaveBalance:
    """Ensures employee leave balances exist for `year` and locks the target balance FOR UPDATE."""
    await initialize_employee_leave_balances(
        db,
        tenant_id=tenant_id,
        employee_id=employee_id,
        year=year,
        actor_id=actor_id,
    )
    bal = (
        await db.execute(
            select(LeaveBalance)
            .where(
                LeaveBalance.tenant_id == tenant_id,
                LeaveBalance.employee_id == employee_id,
                LeaveBalance.leave_type_id == leave_type_id,
                LeaveBalance.year == year,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if bal is None:
        # Fallback if leave type was inactive/custom without init
        await db.execute(
            pg_insert(LeaveBalance)
            .values(
                tenant_id=tenant_id,
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                year=year,
                allocated_days=Decimal("0.00"),
                carried_forward_days=Decimal("0.00"),
                reserved_days=Decimal("0.00"),
                used_days=Decimal("0.00"),
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "employee_id", "leave_type_id", "year"]
            )
        )
        bal = (
            await db.execute(
                select(LeaveBalance)
                .where(
                    LeaveBalance.tenant_id == tenant_id,
                    LeaveBalance.employee_id == employee_id,
                    LeaveBalance.leave_type_id == leave_type_id,
                    LeaveBalance.year == year,
                )
                .with_for_update()
            )
        ).scalar_one()
    return bal


async def evaluate_leave_policy_from_db(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    employee: Employee,
    leave_type: LeaveType,
    start_date: date,
    end_date: date,
    as_of_date: date,
    is_half_day: bool = False,
    half_day_period: Literal["morning", "afternoon"] | None = None,
    document_id: UUID | None = None,
    actor_id: UUID | str | None = None,
    locked_balance: LeaveBalance | None = None,
) -> tuple[PolicyCheckResult, Decimal]:
    """
    Loads `work_week`, `holidays`, `leave_balances`, overlapping `leave_requests`, and
    department `TeamCoverageState`, then evaluates pure `check_leave(...)`.
    """
    await LeaveConfigService.get_or_create_work_week(db, tenant_id, actor_id=actor_id)
    ww = (await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == tenant_id))).scalar_one()

    await LeaveConfigService.ensure_default_pk_holidays(db, tenant_id, year=start_date.year)
    if end_date.year != start_date.year:
        await LeaveConfigService.ensure_default_pk_holidays(db, tenant_id, year=end_date.year)

    holiday_dates = list(
        (
            await db.execute(
                select(Holiday.holiday_date).where(
                    Holiday.tenant_id == tenant_id,
                    Holiday.is_optional.is_(False),
                    Holiday.holiday_date >= start_date,
                    Holiday.holiday_date <= end_date,
                )
            )
        )
        .scalars()
        .all()
    )

    if locked_balance is not None:
        bal = locked_balance
    else:
        await initialize_employee_leave_balances(
            db,
            tenant_id=tenant_id,
            employee_id=employee.id,
            year=start_date.year,
            actor_id=actor_id,
        )
        bal = (
            await db.execute(
                select(LeaveBalance).where(
                    LeaveBalance.tenant_id == tenant_id,
                    LeaveBalance.employee_id == employee.id,
                    LeaveBalance.leave_type_id == leave_type.id,
                    LeaveBalance.year == start_date.year,
                )
            )
        ).scalar_one_or_none()

    bal_snap = (
        BalanceSnapshot(
            leave_type_code=leave_type.code,
            year=start_date.year,
            allocated_days=Decimal(str(bal.allocated_days)),
            carried_forward_days=Decimal(str(bal.carried_forward_days)),
            reserved_days=Decimal(str(bal.reserved_days)),
            used_days=Decimal(str(bal.used_days)),
        )
        if bal is not None
        else BalanceSnapshot(leave_type_code=leave_type.code, year=start_date.year)
    )

    existing_rows = (
        (
            await db.execute(
                select(LeaveRequest).where(
                    LeaveRequest.tenant_id == tenant_id,
                    LeaveRequest.employee_id == employee.id,
                    LeaveRequest.status.in_(tuple(ACTIVE_OVERLAP_STATUSES)),
                    LeaveRequest.start_date <= end_date,
                    LeaveRequest.end_date >= start_date,
                )
            )
        )
        .scalars()
        .all()
    )
    existing_spans = [
        ExistingLeaveSpan(
            request_id=r.id,
            start_date=r.start_date,
            end_date=r.end_date,
            status=r.status,
            is_half_day=r.is_half_day,
            half_day_period=r.half_day_period,  # type: ignore[arg-type]
        )
        for r in existing_rows
    ]

    team_state: TeamCoverageState | None = None
    if employee.department_id is not None:
        team_emp_ids = list(
            (
                await db.execute(
                    select(Employee.id).where(
                        Employee.tenant_id == tenant_id,
                        Employee.department_id == employee.department_id,
                        Employee.status.in_(("active", "on_leave")),
                    )
                )
            )
            .scalars()
            .all()
        )
        team_size = len(team_emp_ids)
        if team_size > 0:
            teammate_leaves = (
                await db.execute(
                    select(LeaveRequest.start_date, LeaveRequest.end_date).where(
                        LeaveRequest.tenant_id == tenant_id,
                        LeaveRequest.employee_id.in_(team_emp_ids),
                        LeaveRequest.employee_id != employee.id,
                        LeaveRequest.status.in_(tuple(ACTIVE_OVERLAP_STATUSES)),
                        LeaveRequest.start_date <= end_date,
                        LeaveRequest.end_date >= start_date,
                    )
                )
            ).all()
            absent_by_date: dict[date, int] = {}
            for s_d, e_d in teammate_leaves:
                c = max(s_d, start_date)
                end_c = min(e_d, end_date)
                while c <= end_c:
                    absent_by_date[c] = absent_by_date.get(c, 0) + 1
                    c += timedelta(days=1)

            team_state = TeamCoverageState(
                team_size=team_size,
                absent_by_date=absent_by_date,
                max_team_absent_pct=Decimal(str(ww.max_team_absent_pct)),
                min_team_present=ww.min_team_present,
            )

    check_input = LeaveCheckInput(
        leave_type_code=leave_type.code,
        start_date=start_date,
        end_date=end_date,
        as_of_date=as_of_date,
        is_half_day=is_half_day,
        half_day_period=half_day_period,
        document_id=document_id,
        weekend_days=tuple(ww.weekend_days or ["Saturday", "Sunday"]),
        probation_months=ww.probation_months,
        allow_half_day=leave_type.allow_half_day,
        allow_during_probation=leave_type.allow_during_probation,
        max_consecutive_days=leave_type.max_consecutive_days,
        min_notice_days=leave_type.min_notice_days,
        requires_document_after_days=leave_type.requires_document_after_days,
        is_paid=leave_type.is_paid,
    )
    emp_state = EmployeeCheckState(
        employee_id=employee.id,
        hire_date=employee.join_date,
        status=employee.status,
    )
    result = check_leave(
        request=check_input,
        employee=emp_state,
        balances={leave_type.code: bal_snap},
        holidays=holiday_dates,
        existing_leaves=existing_spans,
        team_state=team_state,
    )
    return result, bal_snap.available_days


async def _serialize_leave_request(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    leave_req: LeaveRequest,
) -> LeaveRequestResponse:
    emp = (
        await db.execute(
            select(Employee).where(
                Employee.tenant_id == tenant_id,
                Employee.id == leave_req.employee_id,
            )
        )
    ).scalar_one()
    lt = (
        await db.execute(
            select(LeaveType).where(
                LeaveType.tenant_id == tenant_id,
                LeaveType.id == leave_req.leave_type_id,
            )
        )
    ).scalar_one()
    dept_name: str | None = None
    if emp.department_id is not None:
        dept_name = (
            await db.execute(
                select(Department.name).where(
                    Department.tenant_id == tenant_id,
                    Department.id == emp.department_id,
                )
            )
        ).scalar_one_or_none()
    hr_exc_status: str | None = None
    if leave_req.hr_exception_approval_id is not None:
        hr_exc_status = (
            await db.execute(
                select(Approval.status).where(
                    Approval.tenant_id == tenant_id,
                    Approval.id == leave_req.hr_exception_approval_id,
                )
            )
        ).scalar_one_or_none()

    return LeaveRequestResponse(
        id=leave_req.id,
        tenant_id=leave_req.tenant_id,
        employee_id=leave_req.employee_id,
        employee_name=emp.full_name,
        employee_email=emp.email,
        department_name=dept_name,
        leave_type_id=leave_req.leave_type_id,
        leave_type_code=lt.code,
        leave_type_name=lt.name,
        start_date=leave_req.start_date,
        end_date=leave_req.end_date,
        is_half_day=leave_req.is_half_day,
        half_day_period=leave_req.half_day_period,
        working_days=leave_req.working_days,
        reason=leave_req.reason,
        document_id=leave_req.document_id,
        policy_check_status=leave_req.policy_check_status,
        policy_check_reasons=list(leave_req.policy_check_reasons or []),
        status=leave_req.status,
        approver_user_id=leave_req.approver_user_id,
        escalated_to_user_id=leave_req.escalated_to_user_id,
        hr_exception_approval_id=leave_req.hr_exception_approval_id,
        hr_exception_status=hr_exc_status,
        workflow_id=leave_req.workflow_id,
        calendar_event_id=leave_req.calendar_event_id,
        sheets_row_synced=leave_req.sheets_row_synced,
        decided_by=leave_req.decided_by,
        decided_at=leave_req.decided_at,
        decision_note=leave_req.decision_note,
        created_at=leave_req.created_at,
        updated_at=leave_req.updated_at,
    )


async def _enqueue_leave_outbox_once(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    event_type: str,
    leave_request_id: UUID,
    payload: dict[str, Any],
) -> bool:
    """Inserts an outbox event ONLY if no event with `(tenant_id, event_type, leave_request_id)` exists."""
    existing = (
        await db.execute(
            text(
                """
                SELECT id FROM public.outbox_events
                WHERE tenant_id = :tenant_id
                  AND event_type = :event_type
                  AND event_payload->>'leave_request_id' = :req_id
                LIMIT 1;
                """
            ),
            {
                "tenant_id": tenant_id,
                "event_type": event_type,
                "req_id": str(leave_request_id),
            },
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    await enqueue_outbox_event(
        session=db,
        tenant_id=tenant_id,
        event_type=event_type,
        event_payload={"leave_request_id": str(leave_request_id), **payload},
    )
    return True


async def _enqueue_leave_sheets_once(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    leave_req: LeaveRequest,
    emp: Employee,
    lt: LeaveType,
    action: str,
    status_label: str,
) -> bool:
    """Idempotently enqueues a Google Sheets Leave view sync row."""
    existing = (
        await db.execute(
            select(SheetsSyncQueue.id)
            .where(
                SheetsSyncQueue.tenant_id == tenant_id,
                SheetsSyncQueue.entity_type == "leave_request",
                SheetsSyncQueue.entity_id == str(leave_req.id),
                SheetsSyncQueue.action == action,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    await SheetsSyncService.enqueue_sync(
        session=db,
        tenant_id=tenant_id,
        entity_type="leave_request",
        entity_id=str(leave_req.id),
        action=action,
        sheet_name="Leave",
        payload={
            "leave_request_id": str(leave_req.id),
            "employee_id": emp.employee_code or str(emp.id)[:8].upper(),
            "employee_name": emp.full_name,
            "leave_type": lt.code,
            "start_date": leave_req.start_date.isoformat(),
            "end_date": leave_req.end_date.isoformat(),
            "working_days": str(leave_req.working_days),
            "status": status_label,
        },
    )
    return True


async def _safe_send_leave_email(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    clerk_user_id: str | None,
    to_email: str | None,
    template_name: str,
    template_variables: dict[str, Any],
    idempotency_key: str,
    actor_id: UUID | str | None,
) -> dict[str, Any]:
    """Post-commit Gmail connector helper honoring Pass 2 401 circuit-breaker invariants."""
    if not to_email or to_email.strip().lower().endswith(".internal"):
        return {"status": "skipped", "reason": "unroutable_or_missing_email"}

    try:
        res = await EmailDeliveryService.send_email(
            session=db,
            tenant_id=tenant_id,
            to_email=to_email,
            template_name=template_name,
            template_variables=template_variables,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
        )
        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return res
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return {"status": "skipped", "reason": "auth_expired", "detail": str(exc)}
    except GmailDeliveryError as exc:
        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return {"status": "skipped", "reason": "delivery_error", "detail": str(exc)}
    except (ReadOnlyConnectorError, CredentialNotFoundError, RecipientBlockedError) as exc:
        await db.rollback()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return {"status": "skipped", "reason": type(exc).__name__, "detail": str(exc)}
    except Exception as exc:
        logger.warning(f"Leave email dispatch skipped ({idempotency_key}): {exc}")
        await db.rollback()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return {"status": "skipped", "reason": "unexpected_error", "detail": str(exc)}


async def _signal_leave_workflow(
    workflow_id: str | None,
    signal_name: str,
    *args: Any,
) -> bool:
    if not workflow_id:
        return False
    try:
        client = await get_temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(signal_name, *args)
        return True
    except Exception as exc:
        logger.debug(
            f"Could not signal LeaveRequestWorkflow '{workflow_id}' ({signal_name}): {exc}"
        )
        return False


class LeaveService:
    """Authoritative Leave submission, Temporal approval, reservation release & query service."""

    @staticmethod
    async def submit_leave_request(
        db: AsyncSession,
        actor: AuthenticatedUserContext,
        payload: LeaveRequestCreateRequest,
    ) -> LeaveRequestResponse:
        """
        Submits a leave request with serialized `SELECT ... FOR UPDATE` balance locking
        BEFORE overlap/policy checks, reserves `working_days`, writes `leave_ledger`,
        atomically creates Stage 2 `leave.exception` approval on `VIOLATION`, commits,
        and launches `LeaveRequestWorkflow` (`id = str(request_id)`).
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        emp = await resolve_target_employee(db, actor, employee_id=payload.employee_id)

        await LeaveConfigService.ensure_default_leave_types(db, t_id, actor_id=actor_uuid)
        lt: LeaveType | None = None
        if payload.leave_type_id is not None:
            lt = (
                await db.execute(
                    select(LeaveType).where(
                        LeaveType.tenant_id == t_id,
                        LeaveType.id == payload.leave_type_id,
                        LeaveType.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
        elif payload.leave_type_code:
            lt = (
                await db.execute(
                    select(LeaveType).where(
                        LeaveType.tenant_id == t_id,
                        LeaveType.code == payload.leave_type_code,
                        LeaveType.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()

        if lt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Requested leave type not found or inactive in this tenant.",
            )

        # Refinement 2: Ensure & lock leave_balances row FOR UPDATE BEFORE overlap & balance checks
        locked_bal = await _ensure_and_lock_balance(
            db,
            tenant_id=t_id,
            employee_id=emp.id,
            leave_type_id=lt.id,
            year=payload.start_date.year,
            actor_id=actor_uuid,
        )

        as_of_d = payload.as_of_date or to_karachi(datetime.now(UTC)).date()
        policy_res, _ = await evaluate_leave_policy_from_db(
            db=db,
            tenant_id=t_id,
            employee=emp,
            leave_type=lt,
            start_date=payload.start_date,
            end_date=payload.end_date,
            as_of_date=as_of_d,
            is_half_day=payload.is_half_day,
            half_day_period=payload.half_day_period,
            document_id=payload.document_id,
            actor_id=actor_uuid,
            locked_balance=locked_bal,
        )

        if policy_res.status == "BLOCKED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "LEAVE_POLICY_BLOCKED",
                    "message": policy_res.reasons[0].message
                    if policy_res.reasons
                    else "Leave request blocked by policy.",
                    "reasons": [r.model_dump(mode="json") for r in policy_res.reasons],
                },
            )

        ww = (await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id))).scalar_one()
        approver_user_id = await _resolve_approver_user_id(
            db,
            tenant_id=t_id,
            employee=emp,
            work_week=ww,
        )

        request_id = uuid.uuid4()
        working_days = policy_res.working_days
        reasons_json = [r.model_dump(mode="json") for r in policy_res.reasons]

        # Reserve days on balance + immutable ledger row
        locked_bal.reserved_days = (Decimal(str(locked_bal.reserved_days)) + working_days).quantize(
            Decimal("0.01")
        )
        db.add(
            LeaveLedger(
                tenant_id=t_id,
                employee_id=emp.id,
                leave_type_id=lt.id,
                balance_id=locked_bal.id,
                year=payload.start_date.year,
                entry_type="reserve",
                delta_allocated=Decimal("0.00"),
                delta_reserved=working_days,
                delta_used=Decimal("0.00"),
                reference_type="leave_request",
                reference_id=request_id,
                note=f"Reserved {working_days} day(s) for {lt.code} ({_format_date_range(payload.start_date, payload.end_date)})",
                actor_id=actor_uuid,
                idempotency_key=f"reserve:{request_id}",
            )
        )

        leave_req = LeaveRequest(
            id=request_id,
            tenant_id=t_id,
            employee_id=emp.id,
            leave_type_id=lt.id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            is_half_day=payload.is_half_day,
            half_day_period=payload.half_day_period if payload.is_half_day else None,
            working_days=working_days,
            reason=payload.reason,
            document_id=payload.document_id,
            policy_check_status=policy_res.status,
            policy_check_reasons=reasons_json,
            status="pending",
            approver_user_id=approver_user_id,
            workflow_id=str(request_id),
        )
        db.add(leave_req)
        await db.flush()

        # If VIOLATION -> create Stage 2 leave.exception approval atomically
        if policy_res.status == "VIOLATION":
            from app.services.approval_service import create_approval

            violation_msgs = [
                f"{r.code}: {r.message}" for r in policy_res.reasons if r.status == "VIOLATION"
            ]
            approval = await create_approval(
                db=db,
                current_user=actor,
                request=ApprovalCreateRequest(
                    approval_type="leave.exception",
                    approver_role="hr_head",
                    workflow_id=str(request_id),
                    payload={
                        "leave_request_id": str(request_id),
                        "employee_id": str(emp.id),
                        "employee_name": emp.full_name,
                        "leave_type_code": lt.code,
                        "start_date": payload.start_date.isoformat(),
                        "end_date": payload.end_date.isoformat(),
                        "working_days": float(working_days),
                        "violations": violation_msgs,
                        "justification": payload.reason,
                    },
                ),
                commit=False,
            )
            leave_req.hr_exception_approval_id = approval.id
            await db.flush()

        await _enqueue_leave_outbox_once(
            db,
            tenant_id=t_id,
            event_type="leave.requested",
            leave_request_id=request_id,
            payload={
                "tenant_id": str(t_id),
                "employee_id": str(emp.id),
                "leave_type_code": lt.code,
                "working_days": str(working_days),
                "policy_check_status": policy_res.status,
            },
        )
        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="leave.requested",
            target_type="leave_request",
            target_id=request_id,
            details={
                "employee_id": str(emp.id),
                "leave_type_code": lt.code,
                "start_date": payload.start_date.isoformat(),
                "end_date": payload.end_date.isoformat(),
                "working_days": str(working_days),
                "policy_check_status": policy_res.status,
                "hr_exception_approval_id": (
                    str(leave_req.hr_exception_approval_id)
                    if leave_req.hr_exception_approval_id
                    else None
                ),
            },
        )

        response = await _serialize_leave_request(db, tenant_id=t_id, leave_req=leave_req)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

        if payload.start_workflow:
            try:
                from app.workflows.leave_request_workflow import (
                    LeaveRequestWorkflow,
                    LeaveRequestWorkflowInput,
                )

                client = await get_temporal_client()
                await client.start_workflow(
                    LeaveRequestWorkflow.run,
                    LeaveRequestWorkflowInput(
                        tenant_id=str(t_id),
                        leave_request_id=str(request_id),
                        requires_hr_exception=(policy_res.status == "VIOLATION"),
                        reminder_hours=payload.reminder_hours or 24.0,
                        escalation_hours=payload.escalation_hours or 48.0,
                        expiry_hours=payload.expiry_hours or 72.0,
                    ),
                    id=str(request_id),
                    task_queue=settings.TEMPORAL_TASK_QUEUE,
                )
            except Exception as exc:
                logger.warning(
                    f"Temporal LeaveRequestWorkflow start skipped for {request_id}: {exc}"
                )

        return response

    @staticmethod
    async def notify_leave_requested(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        request_id: UUID,
    ) -> dict[str, Any]:
        """Workflow Step 1 Activity: notifies manager in-app + via Gmail connector."""
        leave_req = (
            await db.execute(
                select(LeaveRequest).where(
                    LeaveRequest.tenant_id == tenant_id,
                    LeaveRequest.id == request_id,
                )
            )
        ).scalar_one_or_none()
        if leave_req is None:
            return {"status": "not_found"}

        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == tenant_id,
                    Employee.id == leave_req.employee_id,
                )
            )
        ).scalar_one()
        lt = (
            await db.execute(
                select(LeaveType).where(
                    LeaveType.tenant_id == tenant_id,
                    LeaveType.id == leave_req.leave_type_id,
                )
            )
        ).scalar_one()

        approver: User | None = None
        if leave_req.approver_user_id is not None:
            approver = (
                await db.execute(select(User).where(User.id == leave_req.approver_user_id))
            ).scalar_one_or_none()

        date_range = _format_date_range(leave_req.start_date, leave_req.end_date)
        req_id_str = str(leave_req.id)
        emp_full_name = emp.full_name
        lt_name = lt.name
        working_days_str = str(leave_req.working_days)
        policy_status = leave_req.policy_check_status
        req_reason = leave_req.reason
        approver_id = approver.id if approver is not None else None
        approver_email = approver.email if approver is not None else None
        approver_name = (approver.name or approver.email) if approver is not None else None

        if approver_id is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=tenant_id,
                recipient_user_id=approver_id,
                recipient_email=approver_email,
                category="approval",
                severity="warning" if policy_status == "VIOLATION" else "info",
                title=f"Leave Request: {emp_full_name} ({lt_name})",
                message=(
                    f"{emp_full_name} requested {working_days_str} day(s) of {lt_name} "
                    f"({date_range}). Policy status: {policy_status}."
                ),
                entity_type="leave_request",
                entity_id=request_id,
                dedupe_key=f"leave:requested:{req_id_str}:{approver_id}",
            )
        await db.commit()
        await set_tenant_context(db, str(tenant_id))

        email_res: dict[str, Any] = {"status": "skipped", "reason": "no_approver"}
        if approver_email is not None:
            email_res = await _safe_send_leave_email(
                db,
                tenant_id=tenant_id,
                clerk_user_id=None,
                to_email=approver_email,
                template_name="leave_request_submitted",
                template_variables={
                    "approver_name": approver_name,
                    "employee_name": emp_full_name,
                    "leave_type_name": lt_name,
                    "date_range": date_range,
                    "working_days": working_days_str,
                    "policy_status": policy_status,
                    "reason": req_reason,
                },
                idempotency_key=f"leave-email-requested:{req_id_str}",
                actor_id=approver_id,
            )

        return {"status": "notified", "email": email_res}

    @staticmethod
    async def send_leave_reminder(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        request_id: UUID,
    ) -> dict[str, Any]:
        """Workflow 24h Activity: sends reminder to manager if request is still pending."""
        leave_req = (
            await db.execute(
                select(LeaveRequest).where(
                    LeaveRequest.tenant_id == tenant_id,
                    LeaveRequest.id == request_id,
                )
            )
        ).scalar_one_or_none()
        if leave_req is None or leave_req.status not in ("pending", "pending_hr_exception"):
            return {"status": "skipped", "reason": "already_decided"}

        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == tenant_id,
                    Employee.id == leave_req.employee_id,
                )
            )
        ).scalar_one()
        lt = (
            await db.execute(
                select(LeaveType).where(
                    LeaveType.tenant_id == tenant_id,
                    LeaveType.id == leave_req.leave_type_id,
                )
            )
        ).scalar_one()
        approver = (
            (
                await db.execute(select(User).where(User.id == leave_req.approver_user_id))
            ).scalar_one_or_none()
            if leave_req.approver_user_id
            else None
        )

        date_range = _format_date_range(leave_req.start_date, leave_req.end_date)
        req_id_str = str(leave_req.id)
        emp_full_name = emp.full_name
        lt_name = lt.name
        approver_id = approver.id if approver is not None else None
        approver_email = approver.email if approver is not None else None
        approver_name = (approver.name or approver.email) if approver is not None else None

        if approver_id is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=tenant_id,
                recipient_user_id=approver_id,
                recipient_email=approver_email,
                category="approval",
                severity="warning",
                title=f"Reminder: Leave Request from {emp_full_name}",
                message=f"24h reminder: {emp_full_name}'s {lt_name} ({date_range}) awaits review.",
                entity_type="leave_request",
                entity_id=request_id,
                dedupe_key=f"leave:reminder24h:{req_id_str}:{approver_id}",
            )
        await write_audit(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            action="leave.reminder_sent",
            target_type="leave_request",
            target_id=request_id,
            details={
                "approver_user_id": str(leave_req.approver_user_id)
                if leave_req.approver_user_id
                else None
            },
        )
        await db.commit()
        await set_tenant_context(db, str(tenant_id))

        if approver_email is not None:
            await _safe_send_leave_email(
                db,
                tenant_id=tenant_id,
                clerk_user_id=None,
                to_email=approver_email,
                template_name="leave_request_reminder",
                template_variables={
                    "approver_name": approver_name,
                    "employee_name": emp_full_name,
                    "leave_type_name": lt_name,
                    "date_range": date_range,
                },
                idempotency_key=f"leave-email-reminder:{req_id_str}",
                actor_id=approver_id,
            )
        return {"status": "reminded"}

    @staticmethod
    async def escalate_leave_request(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        request_id: UUID,
    ) -> dict[str, Any]:
        """Workflow 48h Activity: escalates pending request to `work_week.backup_approver_user_id`."""
        leave_req = (
            await db.execute(
                select(LeaveRequest)
                .where(
                    LeaveRequest.tenant_id == tenant_id,
                    LeaveRequest.id == request_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if leave_req is None or leave_req.status not in ("pending", "pending_hr_exception"):
            return {"status": "skipped", "reason": "already_decided"}

        ww = (
            await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == tenant_id))
        ).scalar_one_or_none()
        backup_user_id = ww.backup_approver_user_id if ww else None
        if backup_user_id is None:
            backup_user_id = (
                await db.execute(
                    select(Membership.user_id)
                    .join(Role, Membership.role_id == Role.id)
                    .where(
                        Membership.tenant_id == tenant_id,
                        Membership.status == "active",
                        or_(Membership.is_hr_head.is_(True), Role.role_key == "owner"),
                    )
                    .order_by(Membership.is_hr_head.desc(), Membership.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()

        leave_req.escalated_to_user_id = backup_user_id
        leave_req.updated_at = datetime.now(UTC)

        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == tenant_id,
                    Employee.id == leave_req.employee_id,
                )
            )
        ).scalar_one()
        lt = (
            await db.execute(
                select(LeaveType).where(
                    LeaveType.tenant_id == tenant_id,
                    LeaveType.id == leave_req.leave_type_id,
                )
            )
        ).scalar_one()
        backup_user = (
            (await db.execute(select(User).where(User.id == backup_user_id))).scalar_one_or_none()
            if backup_user_id
            else None
        )

        date_range = _format_date_range(leave_req.start_date, leave_req.end_date)
        req_id_str = str(leave_req.id)
        emp_full_name = emp.full_name
        lt_name = lt.name
        working_days_str = str(leave_req.working_days)
        backup_id = backup_user.id if backup_user is not None else None
        backup_email = backup_user.email if backup_user is not None else None
        backup_name = (backup_user.name or backup_user.email) if backup_user is not None else None

        if backup_id is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=tenant_id,
                recipient_user_id=backup_id,
                recipient_email=backup_email,
                category="approval",
                severity="critical",
                title=f"Escalated Leave Request: {emp_full_name}",
                message=(
                    f"Leave request from {emp_full_name} ({lt_name}, {date_range}) was escalated to you."
                ),
                entity_type="leave_request",
                entity_id=request_id,
                dedupe_key=f"leave:escalated48h:{req_id_str}:{backup_id}",
            )
        await _enqueue_leave_outbox_once(
            db,
            tenant_id=tenant_id,
            event_type="leave.escalated",
            leave_request_id=request_id,
            payload={
                "tenant_id": str(tenant_id),
                "escalated_to_user_id": str(backup_user_id) if backup_user_id else None,
            },
        )
        await write_audit(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            action="leave.escalated",
            target_type="leave_request",
            target_id=request_id,
            details={"escalated_to_user_id": str(backup_user_id) if backup_user_id else None},
        )
        await db.commit()
        await set_tenant_context(db, str(tenant_id))

        if backup_email is not None:
            await _safe_send_leave_email(
                db,
                tenant_id=tenant_id,
                clerk_user_id=None,
                to_email=backup_email,
                template_name="leave_request_escalated",
                template_variables={
                    "approver_name": backup_name,
                    "employee_name": emp_full_name,
                    "leave_type_name": lt_name,
                    "date_range": date_range,
                    "working_days": working_days_str,
                },
                idempotency_key=f"leave-email-escalated:{req_id_str}",
                actor_id=backup_id,
            )

        return {
            "status": "escalated",
            "escalated_to_user_id": str(backup_user_id) if backup_user_id else None,
        }

    @staticmethod
    async def approve_leave_request_phase1_in_tx(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        leave_req: LeaveRequest,
        decided_by: UUID | str | None,
        decision_note: str | None = None,
    ) -> tuple[Employee, LeaveType, WorkWeek]:
        """
        Executes ALL authoritative PostgreSQL mutations for leave approval inside the caller's
        open transaction (used both by `approve_leave_request` and `approval_service.py`'s
        atomic `leave.exception` hook).
        """
        decider_uuid = UUID(str(decided_by)) if decided_by else None
        bal = (
            await db.execute(
                select(LeaveBalance)
                .where(
                    LeaveBalance.tenant_id == tenant_id,
                    LeaveBalance.employee_id == leave_req.employee_id,
                    LeaveBalance.leave_type_id == leave_req.leave_type_id,
                    LeaveBalance.year == leave_req.start_date.year,
                )
                .with_for_update()
            )
        ).scalar_one()

        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == tenant_id,
                    Employee.id == leave_req.employee_id,
                )
            )
        ).scalar_one()
        lt = (
            await db.execute(
                select(LeaveType).where(
                    LeaveType.tenant_id == tenant_id,
                    LeaveType.id == leave_req.leave_type_id,
                )
            )
        ).scalar_one()
        await LeaveConfigService.get_or_create_work_week(db, tenant_id, actor_id=decider_uuid)
        ww = (
            await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == tenant_id))
        ).scalar_one()

        working_days = Decimal(str(leave_req.working_days)).quantize(Decimal("0.01"))
        deduct_key = f"deduct:{leave_req.id}"
        inserted_ledger_id = (
            await db.execute(
                pg_insert(LeaveLedger)
                .values(
                    tenant_id=tenant_id,
                    employee_id=leave_req.employee_id,
                    leave_type_id=leave_req.leave_type_id,
                    balance_id=bal.id,
                    year=leave_req.start_date.year,
                    entry_type="deduct",
                    delta_allocated=Decimal("0.00"),
                    delta_reserved=-working_days,
                    delta_used=working_days,
                    reference_type="leave_request",
                    reference_id=leave_req.id,
                    note=f"Approved {lt.code} leave deduction ({_format_date_range(leave_req.start_date, leave_req.end_date)})",
                    actor_id=decider_uuid,
                    idempotency_key=deduct_key,
                )
                .on_conflict_do_nothing()
                .returning(LeaveLedger.id)
            )
        ).scalar_one_or_none()

        if inserted_ledger_id is not None:
            bal.reserved_days = max(
                Decimal("0.00"),
                (Decimal(str(bal.reserved_days)) - working_days).quantize(Decimal("0.01")),
            )
            bal.used_days = (Decimal(str(bal.used_days)) + working_days).quantize(Decimal("0.01"))

        # Refinement 3: Compute exact working dates excluding weekends & non-optional holidays
        holiday_dates = list(
            (
                await db.execute(
                    select(Holiday.holiday_date).where(
                        Holiday.tenant_id == tenant_id,
                        Holiday.is_optional.is_(False),
                        Holiday.holiday_date >= leave_req.start_date,
                        Holiday.holiday_date <= leave_req.end_date,
                    )
                )
            )
            .scalars()
            .all()
        )
        working_dates = compute_working_dates(
            start_date=leave_req.start_date,
            end_date=leave_req.end_date,
            weekend_days=ww.weekend_days or ["Saturday", "Sunday"],
            holidays=holiday_dates,
        )
        att_status = "HALF_DAY" if leave_req.is_half_day else "ON_LEAVE"
        for d in working_dates:
            att_stmt = pg_insert(Attendance).values(
                tenant_id=tenant_id,
                employee_id=leave_req.employee_id,
                attendance_date=d,
                status=att_status,
                source="leave_approval",
                notes=f"Approved {lt.name} ({leave_req.id})",
                leave_request_id=leave_req.id,
                locked=False,
                updated_by=decider_uuid,
            )
            att_stmt = att_stmt.on_conflict_do_update(
                index_elements=["tenant_id", "employee_id", "attendance_date"],
                set_={
                    "status": att_status,
                    "source": "leave_approval",
                    "leave_request_id": leave_req.id,
                    "notes": f"Approved {lt.name} ({leave_req.id})",
                    "updated_by": decider_uuid,
                    "updated_at": datetime.now(UTC),
                },
                where=Attendance.locked.is_(False),
            )
            await db.execute(att_stmt)

        await _enqueue_leave_outbox_once(
            db,
            tenant_id=tenant_id,
            event_type="leave.approved",
            leave_request_id=leave_req.id,
            payload={
                "tenant_id": str(tenant_id),
                "employee_id": str(leave_req.employee_id),
                "leave_type_code": lt.code,
                "start_date": leave_req.start_date.isoformat(),
                "end_date": leave_req.end_date.isoformat(),
                "working_days": str(working_days),
            },
        )

        if not leave_req.sheets_row_synced:
            await _enqueue_leave_sheets_once(
                db,
                tenant_id=tenant_id,
                leave_req=leave_req,
                emp=emp,
                lt=lt,
                action="approve",
                status_label="approved",
            )
            leave_req.sheets_row_synced = True

        date_range = _format_date_range(leave_req.start_date, leave_req.end_date)
        if emp.user_id is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=tenant_id,
                recipient_user_id=emp.user_id,
                recipient_email=emp.email,
                category="approval",
                severity="info",
                title=f"Leave Approved: {lt.name}",
                message=f"Your {lt.name} request ({date_range}, {working_days} day(s)) was approved.",
                entity_type="leave_request",
                entity_id=leave_req.id,
                dedupe_key=f"leave:approved:{leave_req.id}:{emp.user_id}",
            )

        if leave_req.status != "approved":
            leave_req.status = "approved"
            leave_req.decided_by = decider_uuid
            leave_req.decided_at = datetime.now(UTC)
            if decision_note is not None:
                leave_req.decision_note = decision_note
            leave_req.updated_at = datetime.now(UTC)
            await write_audit(
                db,
                tenant_id=tenant_id,
                actor_id=decider_uuid,
                action="leave.approved",
                target_type="leave_request",
                target_id=leave_req.id,
                details={
                    "employee_id": str(emp.id),
                    "leave_type_code": lt.code,
                    "working_days": str(working_days),
                    "working_dates_count": len(working_dates),
                },
            )

        await db.flush()
        return emp, lt, ww

    @classmethod
    async def approve_leave_request(
        cls,
        db: AsyncSession,
        *,
        tenant_id: UUID,
        request_id: UUID,
        decided_by: UUID | str | None,
        decision_note: str | None = None,
        clerk_user_id: str | None = None,
        after_phase1_commit_hook: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """
        Single idempotent leave approval unit (ADR 009):
        - Phase 1: Locks `leave_requests -> leave_balances` FOR UPDATE, performs idempotent
          deduction (`deduct:{id}`), writes `ON_LEAVE`/`HALF_DAY` attendance on working dates,
          writes exactly-one `leave.approved` outbox event, enqueues Sheets, creates deduped
          notification, sets `status='approved'`, writes audit log, and COMMITS.
        - Phase 2: Post-commit Calendar event creation (guarded by `calendar_event_id is None`)
          and employee email dispatch (guarded by `IdempotencyGate`).
        """
        leave_req = (
            await db.execute(
                select(LeaveRequest)
                .where(
                    LeaveRequest.tenant_id == tenant_id,
                    LeaveRequest.id == request_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if leave_req is None:
            return {"status": "not_found", "leave_request_id": str(request_id)}

        if leave_req.status in ("rejected", "cancelled", "expired"):
            return {"status": "skipped", "current_status": leave_req.status}

        emp, lt, ww = await cls.approve_leave_request_phase1_in_tx(
            db,
            tenant_id=tenant_id,
            leave_req=leave_req,
            decided_by=decided_by,
            decision_note=decision_note,
        )

        # Snapshot primitive fields BEFORE commit() so Phase 2 never touches expired ORM objects
        req_id = leave_req.id
        req_id_str = str(req_id)
        cal_event_id = leave_req.calendar_event_id
        start_d = leave_req.start_date
        end_d = leave_req.end_date
        date_range = _format_date_range(start_d, end_d)
        working_days_str = str(leave_req.working_days)
        decision_note_str = leave_req.decision_note or "Approved"
        emp_full_name = emp.full_name
        emp_email = emp.email
        lt_name = lt.name
        ww_timezone = ww.timezone or KARACHI_TZ_NAME

        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)

        if after_phase1_commit_hook is not None:
            await after_phase1_commit_hook()

        # Phase 2a: Post-commit Google Calendar event (guarded by calendar_event_id is None & active connector)
        if cal_event_id is None:
            has_google_cred = (
                await db.execute(
                    select(ConnectorCredential.id)
                    .where(
                        ConnectorCredential.tenant_id == tenant_id,
                        ConnectorCredential.provider == "google",
                        ConnectorCredential.status == "connected",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if has_google_cred is not None:
                try:
                    pkt_tz = get_karachi_tz(ww_timezone)
                    start_dt = datetime.combine(start_d, time(9, 0), tzinfo=pkt_tz)
                    end_dt = datetime.combine(end_d, time(17, 0), tzinfo=pkt_tz)
                    cal_rec = await CalendarSchedulingService.create_event(
                        session=db,
                        tenant_id=tenant_id,
                        title=f"Leave: {emp_full_name} ({lt_name})",
                        start_time=start_dt,
                        end_time=end_dt,
                        attendees=[emp_email] if not emp_email.endswith(".internal") else [],
                        description=f"Approved {lt_name} leave ({working_days_str} working day(s)).",
                        location="Out of Office",
                        idempotency_key=f"leave-cal:{req_id_str}",
                        actor_id=decided_by,
                    )
                    req_reload = (
                        await db.execute(
                            select(LeaveRequest).where(
                                LeaveRequest.tenant_id == tenant_id,
                                LeaveRequest.id == req_id,
                            )
                        )
                    ).scalar_one()
                    req_reload.calendar_event_id = cal_rec.google_event_id
                    await db.commit()
                    await set_tenant_context(db, str(tenant_id), clerk_user_id)
                except ConnectorAuthExpiredError:
                    await db.commit()
                    await set_tenant_context(db, str(tenant_id), clerk_user_id)
                except (ReadOnlyConnectorError, CredentialNotFoundError) as exc:
                    logger.info(f"Skipping leave calendar event for {req_id_str}: {exc}")
                    await db.rollback()
                    await set_tenant_context(db, str(tenant_id), clerk_user_id)
                except Exception as exc:
                    logger.warning(f"Calendar event creation skipped for {req_id_str}: {exc}")
                    await db.rollback()
                    await set_tenant_context(db, str(tenant_id), clerk_user_id)

        # Phase 2b: Post-commit Gmail notification to employee
        await _safe_send_leave_email(
            db,
            tenant_id=tenant_id,
            clerk_user_id=clerk_user_id,
            to_email=emp_email,
            template_name="leave_decision_notification",
            template_variables={
                "employee_name": emp_full_name,
                "decision_label": "Approved",
                "leave_type_name": lt_name,
                "date_range": date_range,
                "working_days": working_days_str,
                "decision_note": decision_note_str,
            },
            idempotency_key=f"leave-email-approved:{req_id_str}",
            actor_id=decided_by,
        )

        return {"status": "approved", "leave_request_id": req_id_str}

    @staticmethod
    async def release_leave_reservation_in_tx(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        leave_req: LeaveRequest,
        target_status: Literal["rejected", "cancelled", "expired"],
        actor_id: UUID | str | None,
        note: str | None = None,
    ) -> tuple[Employee, LeaveType] | None:
        """
        Idempotently releases reserved days (`release:{request_id}`) inside an open transaction.
        Returns `(emp, lt)` if transitioned, or `None` if already terminal.
        """
        if leave_req.status in ("approved", "rejected", "cancelled", "expired"):
            return None

        actor_uuid = UUID(str(actor_id)) if actor_id else None
        bal = (
            await db.execute(
                select(LeaveBalance)
                .where(
                    LeaveBalance.tenant_id == tenant_id,
                    LeaveBalance.employee_id == leave_req.employee_id,
                    LeaveBalance.leave_type_id == leave_req.leave_type_id,
                    LeaveBalance.year == leave_req.start_date.year,
                )
                .with_for_update()
            )
        ).scalar_one()
        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == tenant_id,
                    Employee.id == leave_req.employee_id,
                )
            )
        ).scalar_one()
        lt = (
            await db.execute(
                select(LeaveType).where(
                    LeaveType.tenant_id == tenant_id,
                    LeaveType.id == leave_req.leave_type_id,
                )
            )
        ).scalar_one()

        working_days = Decimal(str(leave_req.working_days)).quantize(Decimal("0.01"))
        release_key = f"release:{leave_req.id}"
        inserted_id = (
            await db.execute(
                pg_insert(LeaveLedger)
                .values(
                    tenant_id=tenant_id,
                    employee_id=leave_req.employee_id,
                    leave_type_id=leave_req.leave_type_id,
                    balance_id=bal.id,
                    year=leave_req.start_date.year,
                    entry_type="release",
                    delta_allocated=Decimal("0.00"),
                    delta_reserved=-working_days,
                    delta_used=Decimal("0.00"),
                    reference_type="leave_request",
                    reference_id=leave_req.id,
                    note=f"Released reservation ({target_status}): {note or ''}".strip(),
                    actor_id=actor_uuid,
                    idempotency_key=release_key,
                )
                .on_conflict_do_nothing()
                .returning(LeaveLedger.id)
            )
        ).scalar_one_or_none()

        if inserted_id is not None:
            bal.reserved_days = max(
                Decimal("0.00"),
                (Decimal(str(bal.reserved_days)) - working_days).quantize(Decimal("0.01")),
            )

        leave_req.status = target_status
        leave_req.decided_by = actor_uuid
        leave_req.decided_at = datetime.now(UTC)
        if note is not None:
            leave_req.decision_note = note
        leave_req.updated_at = datetime.now(UTC)

        await _enqueue_leave_outbox_once(
            db,
            tenant_id=tenant_id,
            event_type=f"leave.{target_status}",
            leave_request_id=leave_req.id,
            payload={
                "tenant_id": str(tenant_id),
                "employee_id": str(leave_req.employee_id),
                "leave_type_code": lt.code,
                "working_days": str(working_days),
                "reason": note,
            },
        )
        if emp.user_id is not None:
            date_range = _format_date_range(leave_req.start_date, leave_req.end_date)
            await NotificationService.create_deduped(
                db,
                tenant_id=tenant_id,
                recipient_user_id=emp.user_id,
                recipient_email=emp.email,
                category="approval",
                severity="warning" if target_status in ("rejected", "expired") else "info",
                title=f"Leave {target_status.title()}: {lt.name}",
                message=f"Your {lt.name} request ({date_range}) was {target_status}.",
                entity_type="leave_request",
                entity_id=leave_req.id,
                dedupe_key=f"leave:{target_status}:{leave_req.id}:{emp.user_id}",
            )
        await write_audit(
            db,
            tenant_id=tenant_id,
            actor_id=actor_uuid,
            action=f"leave.{target_status}",
            target_type="leave_request",
            target_id=leave_req.id,
            details={"reason": note, "working_days": str(working_days)},
        )
        await db.flush()
        return emp, lt

    @classmethod
    async def release_leave_reservation(
        cls,
        db: AsyncSession,
        *,
        tenant_id: UUID,
        request_id: UUID,
        target_status: Literal["rejected", "cancelled", "expired"],
        actor_id: UUID | str | None,
        note: str | None = None,
        clerk_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Commits idempotent reservation release and dispatches post-commit email notification."""
        leave_req = (
            await db.execute(
                select(LeaveRequest)
                .where(
                    LeaveRequest.tenant_id == tenant_id,
                    LeaveRequest.id == request_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if leave_req is None:
            return {"status": "not_found", "leave_request_id": str(request_id)}

        transitioned = await cls.release_leave_reservation_in_tx(
            db,
            tenant_id=tenant_id,
            leave_req=leave_req,
            target_status=target_status,
            actor_id=actor_id,
            note=note,
        )
        if transitioned is None:
            return {"status": "skipped", "current_status": leave_req.status}

        emp, lt = transitioned
        req_id_str = str(leave_req.id)
        emp_email = emp.email
        emp_full_name = emp.full_name
        lt_name = lt.name
        date_range = _format_date_range(leave_req.start_date, leave_req.end_date)
        working_days_str = str(leave_req.working_days)

        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)

        await _safe_send_leave_email(
            db,
            tenant_id=tenant_id,
            clerk_user_id=clerk_user_id,
            to_email=emp_email,
            template_name="leave_decision_notification",
            template_variables={
                "employee_name": emp_full_name,
                "decision_label": target_status.title(),
                "leave_type_name": lt_name,
                "date_range": date_range,
                "working_days": working_days_str,
                "decision_note": note or target_status.title(),
            },
            idempotency_key=f"leave-email-{target_status}:{req_id_str}",
            actor_id=actor_id,
        )
        return {"status": target_status, "leave_request_id": req_id_str}

    @staticmethod
    async def cancel_approved_leave(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        request_id: UUID,
        actor_id: UUID | str,
        reason: str | None = None,
        clerk_user_id: str | None = None,
    ) -> LeaveRequestResponse:
        """
        Cancels an already-approved leave request:
        - Blocks with HTTP 409 (`ATTENDANCE_MONTH_LOCKED`) if any affected attendance row
          or spanned month is locked.
        - Reverses ONLY attendance rows where `attendance.leave_request_id == request.id`.
        - Restores `used_days -= working_days` via idempotent `reversal:{request_id}` ledger row.
        - Emits `leave.cancelled` outbox event, queues Sheets update, and audits.
        """
        actor_uuid = UUID(str(actor_id))
        leave_req = (
            await db.execute(
                select(LeaveRequest)
                .where(
                    LeaveRequest.tenant_id == tenant_id,
                    LeaveRequest.id == request_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if leave_req is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Leave request '{request_id}' not found.",
            )

        bal = (
            await db.execute(
                select(LeaveBalance)
                .where(
                    LeaveBalance.tenant_id == tenant_id,
                    LeaveBalance.employee_id == leave_req.employee_id,
                    LeaveBalance.leave_type_id == leave_req.leave_type_id,
                    LeaveBalance.year == leave_req.start_date.year,
                )
                .with_for_update()
            )
        ).scalar_one()

        if leave_req.status == "cancelled":
            return await _serialize_leave_request(db, tenant_id=tenant_id, leave_req=leave_req)
        if leave_req.status != "approved":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel approved leave from status '{leave_req.status}'.",
            )

        # Month-lock & row-lock guard
        spanned_months = _year_months_in_range(leave_req.start_date, leave_req.end_date)
        locked_month = (
            await db.execute(
                select(AttendanceMonthLock.year_month).where(
                    AttendanceMonthLock.tenant_id == tenant_id,
                    AttendanceMonthLock.year_month.in_(spanned_months),
                )
            )
        ).scalar_one_or_none()
        locked_att = (
            await db.execute(
                select(Attendance.id)
                .where(
                    Attendance.tenant_id == tenant_id,
                    Attendance.leave_request_id == leave_req.id,
                    Attendance.locked.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if locked_month is not None or locked_att is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "ATTENDANCE_MONTH_LOCKED",
                    "message": (
                        f"Cannot cancel approved leave because attendance for "
                        f"{locked_month or 'the affected period'} is locked."
                    ),
                },
            )

        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == tenant_id,
                    Employee.id == leave_req.employee_id,
                )
            )
        ).scalar_one()
        lt = (
            await db.execute(
                select(LeaveType).where(
                    LeaveType.tenant_id == tenant_id,
                    LeaveType.id == leave_req.leave_type_id,
                )
            )
        ).scalar_one()

        working_days = Decimal(str(leave_req.working_days)).quantize(Decimal("0.01"))
        rev_key = f"reversal:{leave_req.id}"
        inserted_id = (
            await db.execute(
                pg_insert(LeaveLedger)
                .values(
                    tenant_id=tenant_id,
                    employee_id=leave_req.employee_id,
                    leave_type_id=leave_req.leave_type_id,
                    balance_id=bal.id,
                    year=leave_req.start_date.year,
                    entry_type="reversal",
                    delta_allocated=Decimal("0.00"),
                    delta_reserved=Decimal("0.00"),
                    delta_used=-working_days,
                    reference_type="leave_request",
                    reference_id=leave_req.id,
                    note=f"Reversed approved leave ({reason or 'employee cancelled'})",
                    actor_id=actor_uuid,
                    idempotency_key=rev_key,
                )
                .on_conflict_do_nothing()
                .returning(LeaveLedger.id)
            )
        ).scalar_one_or_none()

        if inserted_id is not None:
            bal.used_days = max(
                Decimal("0.00"),
                (Decimal(str(bal.used_days)) - working_days).quantize(Decimal("0.01")),
            )

        # Refinement 3: Reverse ONLY attendance rows linked to this exact leave_request_id
        await db.execute(
            delete(Attendance).where(
                Attendance.tenant_id == tenant_id,
                Attendance.leave_request_id == leave_req.id,
                Attendance.locked.is_(False),
            )
        )

        leave_req.status = "cancelled"
        leave_req.decided_by = actor_uuid
        leave_req.decided_at = datetime.now(UTC)
        if reason is not None:
            leave_req.decision_note = reason
        leave_req.updated_at = datetime.now(UTC)

        await _enqueue_leave_outbox_once(
            db,
            tenant_id=tenant_id,
            event_type="leave.cancelled",
            leave_request_id=leave_req.id,
            payload={
                "tenant_id": str(tenant_id),
                "employee_id": str(leave_req.employee_id),
                "leave_type_code": lt.code,
                "working_days": str(working_days),
                "reversed_from_approved": True,
                "reason": reason,
            },
        )
        await _enqueue_leave_sheets_once(
            db,
            tenant_id=tenant_id,
            leave_req=leave_req,
            emp=emp,
            lt=lt,
            action="cancel",
            status_label="cancelled",
        )
        if emp.user_id is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=tenant_id,
                recipient_user_id=emp.user_id,
                recipient_email=emp.email,
                category="approval",
                severity="info",
                title=f"Leave Cancelled: {lt.name}",
                message=f"Your approved {lt.name} leave ({_format_date_range(leave_req.start_date, leave_req.end_date)}) was cancelled.",
                entity_type="leave_request",
                entity_id=leave_req.id,
                dedupe_key=f"leave:cancelled_approved:{leave_req.id}:{emp.user_id}",
            )
        await write_audit(
            db,
            tenant_id=tenant_id,
            actor_id=actor_uuid,
            action="leave.cancelled_after_approval",
            target_type="leave_request",
            target_id=leave_req.id,
            details={"reason": reason, "working_days": str(working_days)},
        )

        resp = await _serialize_leave_request(db, tenant_id=tenant_id, leave_req=leave_req)
        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return resp

    @classmethod
    async def decide_leave_request(
        cls,
        db: AsyncSession,
        actor: AuthenticatedUserContext,
        request_id: UUID,
        payload: LeaveDecisionRequest,
    ) -> LeaveRequestResponse:
        """
        Manager / Backup Approver / Owner / HR Head decision endpoint.
        Enforces Refinement 4 strict RBAC & anti-self-approval.
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))

        leave_req = (
            await db.execute(
                select(LeaveRequest)
                .where(
                    LeaveRequest.tenant_id == t_id,
                    LeaveRequest.id == request_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if leave_req is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Leave request '{request_id}' not found.",
            )

        # Refinement 4: Strict approver / escalated backup approver / Owner / HR Head RBAC
        is_assigned_approver = leave_req.approver_user_id == actor_uuid
        is_escalated_approver = leave_req.escalated_to_user_id == actor_uuid
        if not (is_assigned_approver or is_escalated_approver or _is_hr_or_owner(actor)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: you are not the assigned or escalated approver for this leave request.",
            )

        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == t_id,
                    Employee.id == leave_req.employee_id,
                )
            )
        ).scalar_one()
        if emp.user_id is not None and emp.user_id == actor_uuid and actor.role != "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: employees cannot decide their own leave requests.",
            )

        if leave_req.status not in ("pending", "pending_hr_exception"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Leave request is already in terminal status '{leave_req.status}'.",
            )

        workflow_id_str = leave_req.workflow_id

        if payload.decision == "rejected":
            await cls.release_leave_reservation(
                db,
                tenant_id=t_id,
                request_id=request_id,
                target_status="rejected",
                actor_id=actor_uuid,
                note=payload.note,
                clerk_user_id=actor.clerk_user_id,
            )
            await _signal_leave_workflow(
                workflow_id_str,
                "manager_decision",
                "rejected",
                str(actor_uuid),
                payload.note,
            )
            refreshed = (
                await db.execute(
                    select(LeaveRequest).where(
                        LeaveRequest.tenant_id == t_id,
                        LeaveRequest.id == request_id,
                    )
                )
            ).scalar_one()
            return await _serialize_leave_request(db, tenant_id=t_id, leave_req=refreshed)

        # Decision == "approved": check if Stage 2 HR exception approval is pending
        hr_exc_status: str | None = None
        if leave_req.hr_exception_approval_id is not None:
            hr_exc_status = (
                await db.execute(
                    select(Approval.status).where(
                        Approval.tenant_id == t_id,
                        Approval.id == leave_req.hr_exception_approval_id,
                    )
                )
            ).scalar_one_or_none()

        if leave_req.hr_exception_approval_id is not None and hr_exc_status != "approved":
            if hr_exc_status == "rejected":
                await cls.release_leave_reservation(
                    db,
                    tenant_id=t_id,
                    request_id=request_id,
                    target_status="rejected",
                    actor_id=actor_uuid,
                    note="HR exception was rejected.",
                    clerk_user_id=actor.clerk_user_id,
                )
                await _signal_leave_workflow(
                    workflow_id_str,
                    "manager_decision",
                    "rejected",
                    str(actor_uuid),
                    "HR exception rejected",
                )
            else:
                leave_req.status = "pending_hr_exception"
                leave_req.decided_by = actor_uuid
                leave_req.decided_at = datetime.now(UTC)
                if payload.note is not None:
                    leave_req.decision_note = payload.note
                leave_req.updated_at = datetime.now(UTC)
                await write_audit(
                    db,
                    tenant_id=t_id,
                    actor_id=actor_uuid,
                    action="leave.manager_approved_awaiting_hr_exception",
                    target_type="leave_request",
                    target_id=leave_req.id,
                    details={"hr_exception_approval_id": str(leave_req.hr_exception_approval_id)},
                )
                await db.commit()
                await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)
                await _signal_leave_workflow(
                    workflow_id_str,
                    "manager_decision",
                    "approved",
                    str(actor_uuid),
                    payload.note,
                )
        else:
            await cls.approve_leave_request(
                db,
                tenant_id=t_id,
                request_id=request_id,
                decided_by=actor_uuid,
                decision_note=payload.note,
                clerk_user_id=actor.clerk_user_id,
            )
            await _signal_leave_workflow(
                workflow_id_str,
                "manager_decision",
                "approved",
                str(actor_uuid),
                payload.note,
            )

        refreshed = (
            await db.execute(
                select(LeaveRequest).where(
                    LeaveRequest.tenant_id == t_id,
                    LeaveRequest.id == request_id,
                )
            )
        ).scalar_one()
        return await _serialize_leave_request(db, tenant_id=t_id, leave_req=refreshed)

    @classmethod
    async def cancel_leave_request(
        cls,
        db: AsyncSession,
        actor: AuthenticatedUserContext,
        request_id: UUID,
        reason: str | None = None,
    ) -> LeaveRequestResponse:
        """Cancels a pending or approved leave request."""
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))

        leave_req = (
            await db.execute(
                select(LeaveRequest).where(
                    LeaveRequest.tenant_id == t_id,
                    LeaveRequest.id == request_id,
                )
            )
        ).scalar_one_or_none()
        if leave_req is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Leave request '{request_id}' not found.",
            )

        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == t_id,
                    Employee.id == leave_req.employee_id,
                )
            )
        ).scalar_one()
        is_owner_of_req = emp.user_id == actor_uuid or emp.email.lower() == actor.email.lower()
        if not (is_owner_of_req or _is_hr_or_owner(actor)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: only the requesting employee, Owner, or HR Head may cancel this leave request.",
            )

        if leave_req.status in ("pending", "pending_hr_exception"):
            workflow_id_str = leave_req.workflow_id
            await cls.release_leave_reservation(
                db,
                tenant_id=t_id,
                request_id=request_id,
                target_status="cancelled",
                actor_id=actor_uuid,
                note=reason,
                clerk_user_id=actor.clerk_user_id,
            )
            await _signal_leave_workflow(
                workflow_id_str,
                "employee_cancel",
                str(actor_uuid),
                reason,
            )
            refreshed = (
                await db.execute(
                    select(LeaveRequest).where(
                        LeaveRequest.tenant_id == t_id,
                        LeaveRequest.id == request_id,
                    )
                )
            ).scalar_one()
            return await _serialize_leave_request(db, tenant_id=t_id, leave_req=refreshed)

        if leave_req.status == "approved":
            return await cls.cancel_approved_leave(
                db,
                tenant_id=t_id,
                request_id=request_id,
                actor_id=actor_uuid,
                reason=reason,
                clerk_user_id=actor.clerk_user_id,
            )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel leave request in terminal status '{leave_req.status}'.",
        )

    @staticmethod
    async def get_balances(
        db: AsyncSession,
        actor: AuthenticatedUserContext,
        *,
        employee_id: UUID | None = None,
        year: int | None = None,
        self_only: bool = False,
    ) -> list[LeaveBalanceItemResponse]:
        t_id = UUID(str(actor.tenant_id))
        target_year = year or to_karachi(datetime.now(UTC)).year

        if self_only:
            emp = await resolve_target_employee(db, actor)
            await initialize_employee_leave_balances(
                db, tenant_id=t_id, employee_id=emp.id, year=target_year, actor_id=actor.user_id
            )
            emp_filter = [LeaveBalance.employee_id == emp.id]
        else:
            emp_filter = [LeaveBalance.employee_id == employee_id] if employee_id else []

        rows = (
            await db.execute(
                select(LeaveBalance, Employee.full_name, LeaveType)
                .join(Employee, LeaveBalance.employee_id == Employee.id)
                .join(LeaveType, LeaveBalance.leave_type_id == LeaveType.id)
                .where(
                    LeaveBalance.tenant_id == t_id,
                    LeaveBalance.year == target_year,
                    *emp_filter,
                )
                .order_by(Employee.full_name.asc(), LeaveType.code.asc())
            )
        ).all()

        items: list[LeaveBalanceItemResponse] = []
        for bal, emp_name, lt in rows:
            available = (
                Decimal(str(bal.allocated_days))
                + Decimal(str(bal.carried_forward_days))
                - Decimal(str(bal.reserved_days))
                - Decimal(str(bal.used_days))
            ).quantize(Decimal("0.01"))
            items.append(
                LeaveBalanceItemResponse(
                    id=bal.id,
                    tenant_id=bal.tenant_id,
                    employee_id=bal.employee_id,
                    employee_name=emp_name,
                    leave_type_id=bal.leave_type_id,
                    leave_type_code=lt.code,
                    leave_type_name=lt.name,
                    year=bal.year,
                    allocated_days=bal.allocated_days,
                    carried_forward_days=bal.carried_forward_days,
                    reserved_days=bal.reserved_days,
                    used_days=bal.used_days,
                    available_days=available,
                    is_paid=lt.is_paid,
                    accrual_mode=lt.accrual_mode,
                )
            )
        return items

    @staticmethod
    async def list_my_requests(
        db: AsyncSession,
        actor: AuthenticatedUserContext,
    ) -> list[LeaveRequestResponse]:
        t_id = UUID(str(actor.tenant_id))
        emp = await resolve_target_employee(db, actor)
        rows = (
            (
                await db.execute(
                    select(LeaveRequest)
                    .where(
                        LeaveRequest.tenant_id == t_id,
                        LeaveRequest.employee_id == emp.id,
                    )
                    .order_by(LeaveRequest.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [await _serialize_leave_request(db, tenant_id=t_id, leave_req=r) for r in rows]

    @staticmethod
    async def list_manager_inbox(
        db: AsyncSession,
        actor: AuthenticatedUserContext,
    ) -> list[LeaveRequestResponse]:
        """
        Refinement 4: Owner/HR Head sees all pending/pending_hr_exception requests;
        managers/backup approvers see ONLY requests where `approver_user_id == actor.user_id`
        OR `escalated_to_user_id == actor.user_id`.
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        stmt = select(LeaveRequest).where(
            LeaveRequest.tenant_id == t_id,
            LeaveRequest.status.in_(("pending", "pending_hr_exception")),
        )
        if not _is_hr_or_owner(actor):
            stmt = stmt.where(
                or_(
                    LeaveRequest.approver_user_id == actor_uuid,
                    LeaveRequest.escalated_to_user_id == actor_uuid,
                )
            )
        rows = (await db.execute(stmt.order_by(LeaveRequest.created_at.desc()))).scalars().all()
        return [await _serialize_leave_request(db, tenant_id=t_id, leave_req=r) for r in rows]

    @staticmethod
    async def list_team_calendar(
        db: AsyncSession,
        actor: AuthenticatedUserContext,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        department_id: UUID | None = None,
    ) -> list[TeamCalendarItemResponse]:
        t_id = UUID(str(actor.tenant_id))
        stmt = (
            select(LeaveRequest, Employee, LeaveType, Department.name)
            .join(Employee, LeaveRequest.employee_id == Employee.id)
            .join(LeaveType, LeaveRequest.leave_type_id == LeaveType.id)
            .outerjoin(
                Department,
                and_(
                    Employee.department_id == Department.id,
                    Department.tenant_id == t_id,
                ),
            )
            .where(
                LeaveRequest.tenant_id == t_id,
                LeaveRequest.status.in_(("approved", "pending", "pending_hr_exception")),
            )
        )
        if start_date is not None:
            stmt = stmt.where(LeaveRequest.end_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(LeaveRequest.start_date <= end_date)
        if department_id is not None:
            stmt = stmt.where(Employee.department_id == department_id)

        rows = (await db.execute(stmt.order_by(LeaveRequest.start_date.asc()))).all()
        return [
            TeamCalendarItemResponse(
                request_id=lr.id,
                employee_id=emp.id,
                employee_name=emp.full_name,
                department_name=dept_name,
                leave_type_code=lt.code,
                leave_type_name=lt.name,
                start_date=lr.start_date,
                end_date=lr.end_date,
                is_half_day=lr.is_half_day,
                half_day_period=lr.half_day_period,
                working_days=lr.working_days,
                status=lr.status,
            )
            for lr, emp, lt, dept_name in rows
        ]

    @staticmethod
    async def get_hr_overview(
        db: AsyncSession,
        actor: AuthenticatedUserContext,
        *,
        status_filter: str | None = None,
    ) -> LeaveOverviewResponse:
        t_id = UUID(str(actor.tenant_id))
        all_rows = (
            (
                await db.execute(
                    select(LeaveRequest)
                    .where(LeaveRequest.tenant_id == t_id)
                    .order_by(LeaveRequest.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        counts = {
            "pending": 0,
            "pending_hr_exception": 0,
            "approved": 0,
            "rejected": 0,
            "cancelled": 0,
            "expired": 0,
        }
        for r in all_rows:
            if r.status in counts:
                counts[r.status] += 1

        filtered = [r for r in all_rows if r.status == status_filter] if status_filter else all_rows
        serialized = [
            await _serialize_leave_request(db, tenant_id=t_id, leave_req=r) for r in filtered
        ]
        return LeaveOverviewResponse(
            total_requests=len(all_rows),
            pending_count=counts["pending"],
            pending_hr_exception_count=counts["pending_hr_exception"],
            approved_count=counts["approved"],
            rejected_count=counts["rejected"],
            cancelled_count=counts["cancelled"],
            expired_count=counts["expired"],
            requests=serialized,
        )
