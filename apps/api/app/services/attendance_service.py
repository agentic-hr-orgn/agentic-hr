"""
Attendance Management, Two-Phase Excel Import, Monthly Auto-Fill, Gap Review,
Month-Lock Governance, and Post-Lock Edit Service for Agentic HR Core (Stage 6 Prompt 4).

Complies with ADR 002 (Forced RLS), ADR 005 (Postgres authoritative, Sheets synced view),
and ADR 009 (Month-Lock Governance & `attendance.post_lock_edit`).
"""

from __future__ import annotations

import calendar
import io
import json
import logging
import re
import time
import uuid
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import openpyxl
from fastapi import HTTPException, status
from redis.exceptions import RedisError
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis_client
from app.core.security import AuthenticatedUserContext
from app.core.timezones import KARACHI_TZ_NAME, get_karachi_tz
from app.models.attendance import Attendance, AttendanceMonthLock
from app.models.department import Department
from app.models.employee import Employee
from app.models.leave import Holiday, LeaveRequest, WorkWeek
from app.models.tenant import set_tenant_context
from app.schemas.approval import ApprovalCreateRequest
from app.schemas.attendance import (
    AttendanceAutoFillResponse,
    AttendanceCheckInRequest,
    AttendanceImportCommitResponse,
    AttendanceImportPreviewRow,
    AttendanceImportRowError,
    AttendanceImportValidationResult,
    AttendanceMonthLockResponse,
    AttendanceMonthReviewResponse,
    AttendancePostLockEditRequest,
    AttendancePostLockEditResponse,
    AttendanceRecordResponse,
    AttendanceTodayResponse,
    DailyAttendanceGridResponse,
    DailyAttendanceGridRow,
    DailyAttendanceUpsertRequest,
    DailyAttendanceUpsertResponse,
    EmployeeMonthGapItem,
)
from app.services.approval_service import create_approval
from app.services.audit_service import write_audit
from app.services.leave_config_service import LeaveConfigService
from app.services.leave_service import resolve_target_employee
from app.services.outbox_service import enqueue_outbox_event
from app.services.sheets_sync_service import SheetsSyncService
from app.services.storage_helper import XLSX_MIME, validate_file_magic_bytes

logger = logging.getLogger("agentic_hr.services.attendance")

VALID_ATTENDANCE_STATUSES: tuple[str, ...] = (
    "PRESENT",
    "ABSENT",
    "HALF_DAY",
    "ON_LEAVE",
    "WFH",
    "HOLIDAY",
    "WEEKEND",
)

ATTENDANCE_IMPORT_COLUMNS: tuple[str, ...] = (
    "employee_email",
    "date",
    "status",
    "notes",
)
ATTENDANCE_REQUIRED_COLUMNS: tuple[str, ...] = (
    "employee_email",
    "date",
    "status",
)
MAX_ATTENDANCE_IMPORT_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_ATTENDANCE_IMPORT_ROWS = 2000
STAGING_TTL_SECONDS = 1800
COMMIT_LOCK_TTL_SECONDS = 60

_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_MEMORY_ATTENDANCE_STAGING: dict[str, tuple[float, str]] = {}
_MEMORY_ATTENDANCE_COMMIT_LOCKS: dict[str, float] = {}


def _is_hr_or_owner(actor: AuthenticatedUserContext) -> bool:
    return bool(actor.role == "owner" or actor.is_hr_head)


def validate_year_month(year_month: str) -> tuple[int, int, date, date]:
    """Validates `YYYY-MM` and returns `(year, month, month_start, month_end)`."""
    clean = (year_month or "").strip()
    m = _YEAR_MONTH_RE.match(clean)
    if not m:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"INVALID_YEAR_MONTH: Expected YYYY-MM format, got '{year_month}'.",
        )
    yr = int(m.group(1))
    mo = int(m.group(2))
    last_day = calendar.monthrange(yr, mo)[1]
    return yr, mo, date(yr, mo, 1), date(yr, mo, last_day)


def _normalize_date_cell(raw_val: Any) -> tuple[str, str | None]:
    """
    Normalizes an Excel date cell (native `datetime`, `date`, or ISO string) into
    `(display_str, iso_YYYY_MM_DD | None)`.
    """
    if raw_val is None:
        return "", None
    if isinstance(raw_val, datetime):
        iso = raw_val.date().isoformat()
        return iso, iso
    if isinstance(raw_val, date):
        iso = raw_val.isoformat()
        return iso, iso
    s = str(raw_val).strip()
    if not s:
        return "", None
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        candidate = s[:10]
        try:
            parsed = date.fromisoformat(candidate)
            if candidate == s or s[10:].strip() in ("00:00:00", "T00:00:00"):
                return candidate, parsed.isoformat()
        except ValueError:
            return s, None
    return s, None


def _staging_redis_key(tenant_id: UUID, batch_id: str) -> str:
    return f"stage6:att_import:{tenant_id}:{batch_id}"


def _commit_lock_redis_key(tenant_id: UUID, batch_id: str) -> str:
    return f"stage6:att_import_lock:{tenant_id}:{batch_id}"


async def _save_staging_payload(tenant_id: UUID, batch_id: str, payload: dict[str, Any]) -> None:
    key = _staging_redis_key(tenant_id, batch_id)
    serialized = json.dumps(payload)
    _MEMORY_ATTENDANCE_STAGING[key] = (time.time() + STAGING_TTL_SECONDS, serialized)
    try:
        redis = await get_redis_client()
        await redis.set(key, serialized, ex=STAGING_TTL_SECONDS)
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis attendance staging write fell back to memory for {key}: {exc}")


async def _load_staging_payload(tenant_id: UUID, batch_id: str) -> dict[str, Any] | None:
    key = _staging_redis_key(tenant_id, batch_id)
    raw_json: str | None = None
    try:
        redis = await get_redis_client()
        raw_json = await redis.get(key)
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis attendance staging read fell back to memory for {key}: {exc}")

    if not raw_json:
        entry = _MEMORY_ATTENDANCE_STAGING.get(key)
        if entry:
            expires_at, val = entry
            if time.time() <= expires_at:
                raw_json = val
            else:
                _MEMORY_ATTENDANCE_STAGING.pop(key, None)

    if not raw_json:
        return None
    data = json.loads(raw_json)
    if data.get("tenant_id") != str(tenant_id):
        return None
    return data


async def _delete_staging_payload(tenant_id: UUID, batch_id: str) -> None:
    key = _staging_redis_key(tenant_id, batch_id)
    _MEMORY_ATTENDANCE_STAGING.pop(key, None)
    try:
        redis = await get_redis_client()
        await redis.delete(key)
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis attendance staging delete fell back to memory for {key}: {exc}")


async def _acquire_commit_lock(tenant_id: UUID, batch_id: str) -> bool:
    key = _commit_lock_redis_key(tenant_id, batch_id)
    now = time.time()
    mem_exp = _MEMORY_ATTENDANCE_COMMIT_LOCKS.get(key)
    if mem_exp is not None and now < mem_exp:
        return False
    _MEMORY_ATTENDANCE_COMMIT_LOCKS[key] = now + COMMIT_LOCK_TTL_SECONDS
    try:
        redis = await get_redis_client()
        acquired = await redis.set(key, "1", nx=True, ex=COMMIT_LOCK_TTL_SECONDS)
        if not acquired:
            return False
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis attendance commit lock fell back to memory for {key}: {exc}")
    return True


async def _release_commit_lock(tenant_id: UUID, batch_id: str) -> None:
    key = _commit_lock_redis_key(tenant_id, batch_id)
    _MEMORY_ATTENDANCE_COMMIT_LOCKS.pop(key, None)
    try:
        redis = await get_redis_client()
        await redis.delete(key)
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis attendance commit unlock fell back to memory for {key}: {exc}")


def _snapshot_attendance_response(
    row: Attendance,
    *,
    employee_name: str,
    employee_email: str,
    department_name: str | None = None,
) -> AttendanceRecordResponse:
    """Builds `AttendanceRecordResponse` from ORM attributes BEFORE `commit()`."""
    return AttendanceRecordResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        employee_id=row.employee_id,
        employee_name=employee_name,
        employee_email=employee_email,
        department_name=department_name,
        attendance_date=row.attendance_date,
        status=row.status,  # type: ignore[arg-type]
        source=row.source,  # type: ignore[arg-type]
        check_in_at=row.check_in_at,
        check_out_at=row.check_out_at,
        notes=row.notes,
        leave_request_id=row.leave_request_id,
        locked=row.locked,
        updated_by=row.updated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _get_month_lock(
    db: AsyncSession,
    tenant_id: UUID,
    year_month: str,
) -> AttendanceMonthLock | None:
    return (
        await db.execute(
            select(AttendanceMonthLock).where(
                AttendanceMonthLock.tenant_id == tenant_id,
                AttendanceMonthLock.year_month == year_month,
            )
        )
    ).scalar_one_or_none()


async def _assert_month_unlocked(
    db: AsyncSession,
    tenant_id: UUID,
    target_date_or_ym: date | str,
) -> str:
    ym = (
        target_date_or_ym.strftime("%Y-%m")
        if isinstance(target_date_or_ym, date)
        else target_date_or_ym
    )
    lock_row = await _get_month_lock(db, tenant_id, ym)
    if lock_row is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"ATTENDANCE_MONTH_LOCKED: Attendance for '{ym}' is locked for payroll. "
                "Submit an attendance.post_lock_edit approval request instead."
            ),
        )
    return ym


async def _load_department_names(db: AsyncSession, tenant_id: UUID) -> dict[UUID, str]:
    rows = (
        await db.execute(
            select(Department.id, Department.name).where(Department.tenant_id == tenant_id)
        )
    ).all()
    return {d_id: name for d_id, name in rows}


async def _load_active_employees_for_month(
    db: AsyncSession,
    tenant_id: UUID,
    month_end: date,
) -> list[Employee]:
    return list(
        (
            await db.execute(
                select(Employee)
                .where(
                    Employee.tenant_id == tenant_id,
                    Employee.status == "active",
                    or_(Employee.join_date.is_(None), Employee.join_date <= month_end),
                )
                .order_by(Employee.full_name.asc(), Employee.email.asc())
            )
        )
        .scalars()
        .all()
    )


async def _load_approved_leave_map(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    month_start: date,
    month_end: date,
) -> dict[tuple[UUID, date], tuple[UUID, str]]:
    """
    Maps `(employee_id, date) -> (leave_request_id, 'HALF_DAY' | 'ON_LEAVE')` for
    all approved `LeaveRequest` spans overlapping `[month_start, month_end]`.
    """
    rows = (
        await db.execute(
            select(
                LeaveRequest.id,
                LeaveRequest.employee_id,
                LeaveRequest.start_date,
                LeaveRequest.end_date,
                LeaveRequest.is_half_day,
            ).where(
                LeaveRequest.tenant_id == tenant_id,
                LeaveRequest.status == "approved",
                LeaveRequest.start_date <= month_end,
                LeaveRequest.end_date >= month_start,
            )
        )
    ).all()
    leave_map: dict[tuple[UUID, date], tuple[UUID, str]] = {}
    for req_id, emp_id, s_d, e_d, is_half in rows:
        curr = max(s_d, month_start)
        end_c = min(e_d, month_end)
        att_status = "HALF_DAY" if is_half else "ON_LEAVE"
        while curr <= end_c:
            leave_map[(emp_id, curr)] = (req_id, att_status)
            curr = date.fromordinal(curr.toordinal() + 1)
    return leave_map


class AttendanceService:
    """Orchestrates Stage 6 Attendance Entry, Import, Auto-Fill, Month Review & Locking."""

    @classmethod
    async def get_my_today(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
    ) -> AttendanceTodayResponse:
        """
        Returns today's Asia/Karachi date, `check_in_enabled`, `is_locked`, and the caller's
        existing attendance record (if any).
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        await LeaveConfigService.get_or_create_work_week(db, t_id, actor_id=actor_uuid)
        ww = (await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id))).scalar_one()

        pkt_tz = get_karachi_tz(ww.timezone or KARACHI_TZ_NAME)
        today_pkt = datetime.now(pkt_tz).date()
        ym = today_pkt.strftime("%Y-%m")
        check_in_enabled = bool(ww.check_in_enabled)
        is_locked = (await _get_month_lock(db, t_id, ym)) is not None

        emp = await resolve_target_employee(db, actor)
        dept_names = await _load_department_names(db, t_id)
        row = (
            await db.execute(
                select(Attendance).where(
                    Attendance.tenant_id == t_id,
                    Attendance.employee_id == emp.id,
                    Attendance.attendance_date == today_pkt,
                )
            )
        ).scalar_one_or_none()

        record_dto = (
            _snapshot_attendance_response(
                row,
                employee_name=emp.full_name,
                employee_email=emp.email,
                department_name=dept_names.get(emp.department_id) if emp.department_id else None,
            )
            if row is not None
            else None
        )

        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)

        return AttendanceTodayResponse(
            today_date=today_pkt,
            year_month=ym,
            check_in_enabled=check_in_enabled,
            is_locked=is_locked,
            record=record_dto,
        )

    @classmethod
    async def check_in(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        payload: AttendanceCheckInRequest,
    ) -> AttendanceRecordResponse:
        """
        Employee self check-in / check-out. Enforces `work_week.check_in_enabled` and month lock,
        is idempotent on same-day repeats, and queues an `Attendance` Sheets sync row.
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        emp = await resolve_target_employee(db, actor)

        await LeaveConfigService.get_or_create_work_week(db, t_id, actor_id=actor_uuid)
        ww = (await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id))).scalar_one()
        if not ww.check_in_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CHECK_IN_DISABLED: Self check-in is disabled by organization policy.",
            )

        pkt_tz = get_karachi_tz(ww.timezone or KARACHI_TZ_NAME)
        now_pkt = datetime.now(pkt_tz)
        now_utc = datetime.now(UTC)
        target_date = payload.target_date or now_pkt.date()
        await _assert_month_unlocked(db, t_id, target_date)

        is_checkout = bool(payload.check_out or payload.action == "check_out")
        existing = (
            await db.execute(
                select(Attendance)
                .where(
                    Attendance.tenant_id == t_id,
                    Attendance.employee_id == emp.id,
                    Attendance.attendance_date == target_date,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if existing is not None:
            if existing.locked:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="ATTENDANCE_MONTH_LOCKED: Target attendance record is locked.",
                )
            existing.status = "PRESENT"
            existing.source = "check_in"
            if existing.check_in_at is None:
                existing.check_in_at = now_utc
            if is_checkout:
                existing.check_out_at = now_utc
            if payload.notes is not None:
                existing.notes = payload.notes
            existing.updated_by = actor_uuid
            existing.updated_at = now_utc
            att_row = existing
        else:
            att_row = Attendance(
                tenant_id=t_id,
                employee_id=emp.id,
                attendance_date=target_date,
                status="PRESENT",
                source="check_in",
                check_in_at=now_utc,
                check_out_at=now_utc if is_checkout else None,
                notes=payload.notes,
                locked=False,
                updated_by=actor_uuid,
            )
            db.add(att_row)

        await db.flush()

        emp_id_str = str(emp.id)
        emp_code_or_id = emp.employee_code or emp_id_str[:8].upper()
        emp_name = emp.full_name
        emp_email = emp.email
        dept_names = await _load_department_names(db, t_id)
        dept_name = dept_names.get(emp.department_id) if emp.department_id else None

        await SheetsSyncService.enqueue_attendance_sync(
            session=db,
            tenant_id=t_id,
            entity_id=f"{emp.id}:{target_date.isoformat()}",
            action="check_out" if is_checkout else "check_in",
            payload={
                "attendance_date": target_date.isoformat(),
                "employee_id": emp_code_or_id,
                "employee_name": emp_name,
                "status": att_row.status,
                "source": att_row.source,
                "locked": att_row.locked,
            },
        )
        await write_audit(
            session=db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="attendance.checked_out" if is_checkout else "attendance.checked_in",
            target_type="attendance",
            target_id=att_row.id,
            details={
                "employee_id": emp_id_str,
                "attendance_date": target_date.isoformat(),
                "status": att_row.status,
                "check_out": is_checkout,
            },
        )

        dto = _snapshot_attendance_response(
            att_row,
            employee_name=emp_name,
            employee_email=emp_email,
            department_name=dept_name,
        )
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)
        return dto

    @classmethod
    async def get_daily_grid(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        target_date: date,
        department_id: UUID | None = None,
    ) -> DailyAttendanceGridResponse:
        """
        Returns the manager/HR daily attendance grid for `target_date`.
        Plain managers see only their direct reports; Owner / HR Head see all active employees.
        """
        t_id = UUID(str(actor.tenant_id))
        ym = target_date.strftime("%Y-%m")
        is_locked = (await _get_month_lock(db, t_id, ym)) is not None
        dept_names = await _load_department_names(db, t_id)

        emp_stmt = select(Employee).where(
            Employee.tenant_id == t_id,
            Employee.status == "active",
        )
        if not _is_hr_or_owner(actor):
            caller_emp = await resolve_target_employee(db, actor)
            emp_stmt = emp_stmt.where(Employee.manager_id == caller_emp.id)
        if department_id is not None:
            emp_stmt = emp_stmt.where(Employee.department_id == department_id)

        employees = list(
            (await db.execute(emp_stmt.order_by(Employee.full_name.asc()))).scalars().all()
        )
        emp_ids = [e.id for e in employees]

        att_by_emp: dict[UUID, Attendance] = {}
        if emp_ids:
            att_rows = (
                (
                    await db.execute(
                        select(Attendance).where(
                            Attendance.tenant_id == t_id,
                            Attendance.attendance_date == target_date,
                            Attendance.employee_id.in_(emp_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            att_by_emp = {r.employee_id: r for r in att_rows}

        rows: list[DailyAttendanceGridRow] = []
        marked_count = 0
        for emp in employees:
            att = att_by_emp.get(emp.id)
            if att is not None:
                marked_count += 1
            rows.append(
                DailyAttendanceGridRow(
                    employee_id=emp.id,
                    employee_code=emp.employee_code,
                    employee_name=emp.full_name,
                    employee_email=emp.email,
                    department_name=dept_names.get(emp.department_id)
                    if emp.department_id
                    else None,
                    job_title=emp.job_title,
                    attendance_id=att.id if att else None,
                    attendance_date=target_date,
                    status=att.status if att else None,  # type: ignore[arg-type]
                    source=att.source if att else None,  # type: ignore[arg-type]
                    check_in_at=att.check_in_at if att else None,
                    check_out_at=att.check_out_at if att else None,
                    notes=att.notes if att else None,
                    locked=att.locked if att else is_locked,
                )
            )

        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)

        return DailyAttendanceGridResponse(
            target_date=target_date,
            year_month=ym,
            is_locked=is_locked,
            total_employees=len(rows),
            marked_count=marked_count,
            unmarked_count=len(rows) - marked_count,
            rows=rows,
        )

    @classmethod
    async def upsert_daily_grid(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        payload: DailyAttendanceUpsertRequest,
    ) -> DailyAttendanceUpsertResponse:
        """Batch upserts daily attendance (`source='manager_grid'`). Refuses locked months (409)."""
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        ym = await _assert_month_unlocked(db, t_id, payload.target_date)

        caller_emp_id: UUID | None = None
        if not _is_hr_or_owner(actor):
            caller_emp = await resolve_target_employee(db, actor)
            caller_emp_id = caller_emp.id

        requested_emp_ids = [item.employee_id for item in payload.entries]
        emp_rows = (
            (
                await db.execute(
                    select(Employee).where(
                        Employee.tenant_id == t_id,
                        Employee.id.in_(requested_emp_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        emp_map = {e.id: e for e in emp_rows}
        dept_names = await _load_department_names(db, t_id)

        for item in payload.entries:
            emp = emp_map.get(item.employee_id)
            if emp is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Employee '{item.employee_id}' not found in tenant.",
                )
            if caller_emp_id is not None and emp.manager_id != caller_emp_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Managers may only mark attendance for their direct reports ({emp.email}).",
                )

        existing_rows = (
            (
                await db.execute(
                    select(Attendance)
                    .where(
                        Attendance.tenant_id == t_id,
                        Attendance.attendance_date == payload.target_date,
                        Attendance.employee_id.in_(requested_emp_ids),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        existing_map = {r.employee_id: r for r in existing_rows}
        for r in existing_rows:
            if r.locked:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="ATTENDANCE_MONTH_LOCKED: Target attendance row is locked.",
                )

        upserted_pairs: list[tuple[Attendance, Employee]] = []
        for item in payload.entries:
            emp = emp_map[item.employee_id]
            att = existing_map.get(item.employee_id)
            if att is not None:
                att.status = item.status
                att.source = "manager_grid"
                att.notes = item.notes
                att.updated_by = actor_uuid
                att.updated_at = datetime.now(UTC)
            else:
                att = Attendance(
                    tenant_id=t_id,
                    employee_id=item.employee_id,
                    attendance_date=payload.target_date,
                    status=item.status,
                    source="manager_grid",
                    notes=item.notes,
                    locked=False,
                    updated_by=actor_uuid,
                )
                db.add(att)
                existing_map[item.employee_id] = att
            upserted_pairs.append((att, emp))

        await db.flush()

        records: list[AttendanceRecordResponse] = []
        for att, emp in upserted_pairs:
            await SheetsSyncService.enqueue_attendance_sync(
                session=db,
                tenant_id=t_id,
                entity_id=f"{emp.id}:{payload.target_date.isoformat()}",
                action="manager_grid",
                payload={
                    "attendance_date": payload.target_date.isoformat(),
                    "employee_id": emp.employee_code or str(emp.id)[:8].upper(),
                    "employee_name": emp.full_name,
                    "status": att.status,
                    "source": att.source,
                    "locked": att.locked,
                },
            )
            records.append(
                _snapshot_attendance_response(
                    att,
                    employee_name=emp.full_name,
                    employee_email=emp.email,
                    department_name=dept_names.get(emp.department_id)
                    if emp.department_id
                    else None,
                )
            )

        await write_audit(
            session=db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="attendance.daily_grid_upserted",
            target_type="attendance",
            target_id=payload.target_date.isoformat(),
            details={
                "target_date": payload.target_date.isoformat(),
                "upserted_count": len(records),
            },
        )

        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)

        return DailyAttendanceUpsertResponse(
            target_date=payload.target_date,
            year_month=ym,
            upserted_count=len(records),
            records=records,
        )

    @classmethod
    async def validate_attendance_xlsx(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        file_bytes: bytes,
        filename: str,
    ) -> AttendanceImportValidationResult:
        """
        Phase 1 of Excel Attendance Import:
        Validates magic bytes, required headers, employee membership, status enum, date format
        (ISO string or native openpyxl `datetime`/`date`), intra-file duplicates, and month locks.
        """
        t_id = UUID(str(actor.tenant_id))
        clean_filename = (filename or "attendance.xlsx").strip()
        if not clean_filename.lower().endswith(".xlsx"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="INVALID_XLSX_FILE: Only .xlsx Excel workbooks are supported.",
            )
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="IMPORT_FILE_EMPTY: Uploaded file cannot be empty.",
            )
        if len(file_bytes) > MAX_ATTENDANCE_IMPORT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"IMPORT_FILE_TOO_LARGE: File size ({len(file_bytes)} bytes) exceeds "
                    f"maximum allowed limit of {MAX_ATTENDANCE_IMPORT_BYTES} bytes (5MB)."
                ),
            )

        validate_file_magic_bytes(
            file_bytes,
            XLSX_MIME,
            error_code="INVALID_XLSX_FILE",
        )

        try:
            wb = openpyxl.load_workbook(
                io.BytesIO(file_bytes),
                read_only=True,
                data_only=True,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"INVALID_XLSX_FILE: Could not parse .xlsx workbook: {exc}",
            ) from exc

        parsed_rows: list[tuple[int, dict[str, Any]]] = []
        try:
            ws = wb["Attendance"] if "Attendance" in wb.sheetnames else wb.active
            row_iter = ws.iter_rows(values_only=True)
            header_tuple = next(row_iter, None)
            if not header_tuple:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="IMPORT_FILE_EMPTY: Workbook contains no header row.",
                )

            header_map: dict[str, int] = {}
            for idx, col_val in enumerate(header_tuple):
                if col_val is not None:
                    norm = str(col_val).strip().lower()
                    if norm and norm not in header_map:
                        header_map[norm] = idx

            missing_headers = [c for c in ATTENDANCE_REQUIRED_COLUMNS if c not in header_map]
            if missing_headers:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"MISSING_REQUIRED_COLUMNS: Workbook is missing required columns: "
                        f"{', '.join(missing_headers)}"
                    ),
                )

            for excel_row_num, row_vals in enumerate(row_iter, start=2):
                if row_vals is None:
                    continue
                row_dict: dict[str, Any] = {}
                is_blank = True
                for col_name in ATTENDANCE_IMPORT_COLUMNS:
                    col_idx = header_map.get(col_name)
                    cell_val = (
                        row_vals[col_idx]
                        if (col_idx is not None and col_idx < len(row_vals))
                        else None
                    )
                    if cell_val is not None and str(cell_val).strip() != "":
                        is_blank = False
                    row_dict[col_name] = cell_val
                if is_blank:
                    continue
                parsed_rows.append((excel_row_num, row_dict))
                if len(parsed_rows) > MAX_ATTENDANCE_IMPORT_ROWS:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"IMPORT_ROW_LIMIT_EXCEEDED: Workbook exceeds maximum allowed "
                            f"{MAX_ATTENDANCE_IMPORT_ROWS} non-empty rows."
                        ),
                    )
        finally:
            wb.close()

        if not parsed_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="IMPORT_FILE_EMPTY: Workbook contains no non-empty attendance rows.",
            )

        emp_rows = (
            await db.execute(
                select(
                    Employee.id, Employee.email, Employee.full_name, Employee.employee_code
                ).where(
                    Employee.tenant_id == t_id,
                    Employee.status == "active",
                )
            )
        ).all()
        emp_by_email: dict[str, tuple[UUID, str, str | None]] = {
            str(email).strip().lower(): (e_id, full_name, e_code)
            for e_id, email, full_name, e_code in emp_rows
        }

        locked_yms = set(
            (
                await db.execute(
                    select(AttendanceMonthLock.year_month).where(
                        AttendanceMonthLock.tenant_id == t_id
                    )
                )
            )
            .scalars()
            .all()
        )

        # Count intra-file (email, iso_date) pairs for duplicate detection
        pair_counts: dict[tuple[str, str], int] = {}
        for _, raw in parsed_rows:
            em = str(raw.get("employee_email") or "").strip().lower()
            _, iso_d = _normalize_date_cell(raw.get("date"))
            if em and iso_d:
                pair = (em, iso_d)
                pair_counts[pair] = pair_counts.get(pair, 0) + 1

        batch_id = uuid.uuid4().hex
        all_errors: list[AttendanceImportRowError] = []
        preview_rows: list[AttendanceImportPreviewRow] = []
        staged_rows: list[dict[str, Any]] = []
        valid_count = 0
        invalid_count = 0

        for r_num, raw in parsed_rows:
            email_norm = str(raw.get("employee_email") or "").strip().lower()
            date_display, date_iso = _normalize_date_cell(raw.get("date"))
            status_norm = str(raw.get("status") or "").strip().upper()
            notes_str = str(raw.get("notes") or "").strip() or None
            row_errs: list[str] = []

            def _err(field: str, msg: str, *, _r: int = r_num, _re: list[str] = row_errs) -> None:
                all_errors.append(AttendanceImportRowError(row_number=_r, field=field, message=msg))
                _re.append(f"{field}: {msg}")

            if not email_norm:
                _err("employee_email", "Required field 'employee_email' is missing.")
            elif email_norm not in emp_by_email:
                _err(
                    "employee_email",
                    f"Active employee with email '{email_norm}' not found in this tenant.",
                )

            if not date_display:
                _err("date", "Required field 'date' is missing.")
            elif not date_iso:
                _err(
                    "date",
                    f"Invalid date '{date_display}'. Expected ISO format YYYY-MM-DD.",
                )
            elif date_iso[:7] in locked_yms:
                _err(
                    "date",
                    f"Month '{date_iso[:7]}' is locked; submit an attendance.post_lock_edit request instead.",
                )

            if not status_norm:
                _err("status", "Required field 'status' is missing.")
            elif status_norm not in VALID_ATTENDANCE_STATUSES:
                _err(
                    "status",
                    f"Invalid status '{status_norm}'. Allowed: {', '.join(VALID_ATTENDANCE_STATUSES)}.",
                )

            if email_norm and date_iso and pair_counts.get((email_norm, date_iso), 0) > 1:
                _err(
                    "date",
                    f"Duplicate entry for '{email_norm}' on '{date_iso}' in uploaded file.",
                )

            is_valid = len(row_errs) == 0
            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1

            preview_rows.append(
                AttendanceImportPreviewRow(
                    row_number=r_num,
                    employee_email=email_norm,
                    attendance_date=date_iso or date_display,
                    status=status_norm,
                    notes=notes_str,
                    is_valid=is_valid,
                    errors=row_errs,
                )
            )
            emp_info = emp_by_email.get(email_norm)
            staged_rows.append(
                {
                    "row_number": r_num,
                    "employee_id": str(emp_info[0]) if emp_info else None,
                    "employee_name": emp_info[1] if emp_info else "",
                    "employee_code": emp_info[2] if emp_info else None,
                    "employee_email": email_norm,
                    "attendance_date": date_iso or date_display,
                    "status": status_norm,
                    "notes": notes_str,
                    "is_valid": is_valid,
                }
            )

        await _save_staging_payload(
            t_id,
            batch_id,
            {
                "import_batch_id": batch_id,
                "tenant_id": str(t_id),
                "file_name": clean_filename,
                "total_rows": len(parsed_rows),
                "valid_rows": valid_count,
                "invalid_rows": invalid_count,
                "rows": staged_rows,
            },
        )

        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)

        return AttendanceImportValidationResult(
            import_batch_id=batch_id,
            file_name=clean_filename,
            total_rows=len(parsed_rows),
            valid_rows=valid_count,
            invalid_rows=invalid_count,
            can_commit=(invalid_count == 0 and valid_count > 0),
            preview_rows=preview_rows,
            errors=all_errors,
        )

    @classmethod
    async def commit_attendance_xlsx(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        import_batch_id: str,
    ) -> AttendanceImportCommitResponse:
        """
        Phase 2 of Excel Attendance Import:
        Acquires Redis NX commit lock, validates zero invalid rows and unlocked months,
        upserts attendance rows (`source='excel_import'`), queues Sheets sync, and audits.
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        clean_batch_id = (import_batch_id or "").strip()

        if not await _acquire_commit_lock(t_id, clean_batch_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"IMPORT_BATCH_COMMIT_IN_PROGRESS: Commit for batch '{clean_batch_id}' "
                    "is already in progress."
                ),
            )

        try:
            staging = await _load_staging_payload(t_id, clean_batch_id)
            if not staging:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"IMPORT_BATCH_NOT_FOUND: Attendance import batch '{clean_batch_id}' "
                        "not found, expired, or already committed."
                    ),
                )

            invalid_rows = int(staging.get("invalid_rows", 0))
            valid_rows = int(staging.get("valid_rows", 0))
            if invalid_rows > 0 or valid_rows == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"IMPORT_BATCH_HAS_ERRORS: Cannot commit batch '{clean_batch_id}' with "
                        f"{invalid_rows} invalid row(s) and {valid_rows} valid row(s)."
                    ),
                )

            rows: list[dict[str, Any]] = staging.get("rows", [])
            yms_touched = sorted({str(r["attendance_date"])[:7] for r in rows})
            for ym in yms_touched:
                await _assert_month_unlocked(db, t_id, ym)

            sheets_queued = 0
            for srow in rows:
                emp_uuid = UUID(str(srow["employee_id"]))
                att_date = date.fromisoformat(str(srow["attendance_date"]))
                att_status = str(srow["status"])
                att_notes = srow.get("notes")

                stmt = pg_insert(Attendance).values(
                    tenant_id=t_id,
                    employee_id=emp_uuid,
                    attendance_date=att_date,
                    status=att_status,
                    source="excel_import",
                    notes=att_notes,
                    locked=False,
                    updated_by=actor_uuid,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["tenant_id", "employee_id", "attendance_date"],
                    set_={
                        "status": att_status,
                        "source": "excel_import",
                        "notes": att_notes,
                        "updated_by": actor_uuid,
                        "updated_at": datetime.now(UTC),
                    },
                    where=Attendance.locked.is_(False),
                )
                await db.execute(stmt)

                queued = await SheetsSyncService.enqueue_attendance_sync(
                    session=db,
                    tenant_id=t_id,
                    entity_id=f"{emp_uuid}:{att_date.isoformat()}",
                    action="excel_import",
                    payload={
                        "attendance_date": att_date.isoformat(),
                        "employee_id": srow.get("employee_code") or str(emp_uuid)[:8].upper(),
                        "employee_name": srow.get("employee_name", ""),
                        "status": att_status,
                        "source": "excel_import",
                        "locked": False,
                    },
                )
                if queued is not None:
                    sheets_queued += 1

            await write_audit(
                session=db,
                tenant_id=t_id,
                actor_id=actor_uuid,
                action="attendance.bulk_imported",
                target_type="attendance_import_batch",
                target_id=clean_batch_id,
                details={
                    "import_batch_id": clean_batch_id,
                    "file_name": staging.get("file_name"),
                    "committed_count": len(rows),
                    "year_months": yms_touched,
                },
            )

            await db.commit()
            await set_tenant_context(db, actor.tenant_id, actor.user_id)
            await _delete_staging_payload(t_id, clean_batch_id)

            return AttendanceImportCommitResponse(
                status="committed",
                import_batch_id=clean_batch_id,
                committed_count=len(rows),
                sheets_queued=sheets_queued,
                year_months=yms_touched,
            )
        finally:
            await _release_commit_lock(t_id, clean_batch_id)

    @classmethod
    async def auto_fill_month(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        year_month: str,
        overwrite_existing: bool = False,
    ) -> AttendanceAutoFillResponse:
        """
        Fills missing dates in `year_month` for eligible active employees (`join_date <= month_end`):
        1. Approved `LeaveRequest` on working days -> `ON_LEAVE` / `HALF_DAY` (`leave_request_id = req.id`)
        2. Non-optional `Holiday` -> `HOLIDAY`
        3. `WorkWeek.weekend_days` -> `WEEKEND`
        Never overwrites existing manual entries unless `overwrite_existing=True`.
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        yr, _mo, month_start, month_end = validate_year_month(year_month)
        await _assert_month_unlocked(db, t_id, year_month)

        await LeaveConfigService.get_or_create_work_week(db, t_id, actor_id=actor_uuid)
        ww = (await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id))).scalar_one()
        weekend_set = {d.strip().lower() for d in (ww.weekend_days or ["Saturday", "Sunday"])}

        await LeaveConfigService.ensure_default_pk_holidays(db, t_id, year=yr)
        holiday_dates = set(
            (
                await db.execute(
                    select(Holiday.holiday_date).where(
                        Holiday.tenant_id == t_id,
                        Holiday.is_optional.is_(False),
                        Holiday.holiday_date >= month_start,
                        Holiday.holiday_date <= month_end,
                    )
                )
            )
            .scalars()
            .all()
        )

        employees = await _load_active_employees_for_month(db, t_id, month_end)
        leave_map = await _load_approved_leave_map(
            db, tenant_id=t_id, month_start=month_start, month_end=month_end
        )

        existing_rows = (
            (
                await db.execute(
                    select(Attendance).where(
                        Attendance.tenant_id == t_id,
                        Attendance.attendance_date >= month_start,
                        Attendance.attendance_date <= month_end,
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_map: dict[tuple[UUID, date], Attendance] = {
            (r.employee_id, r.attendance_date): r for r in existing_rows
        }

        filled_leave = 0
        filled_holiday = 0
        filled_weekend = 0
        skipped_existing = 0

        all_days = [
            date.fromordinal(month_start.toordinal() + offset)
            for offset in range((month_end - month_start).days + 1)
        ]

        for emp in employees:
            for d in all_days:
                existing = existing_map.get((emp.id, d))
                if existing is not None and (existing.locked or not overwrite_existing):
                    skipped_existing += 1
                    continue

                day_name = d.strftime("%A").lower()
                is_weekend = day_name in weekend_set
                is_holiday = d in holiday_dates
                leave_entry = leave_map.get((emp.id, d))

                target_status: str | None = None
                target_source = "auto_fill"
                target_leave_id: UUID | None = None

                if leave_entry is not None and not is_weekend and not is_holiday:
                    target_leave_id, target_status = leave_entry
                    filled_leave += 1
                elif is_holiday:
                    target_status = "HOLIDAY"
                    filled_holiday += 1
                elif is_weekend:
                    target_status = "WEEKEND"
                    filled_weekend += 1
                else:
                    continue

                stmt = pg_insert(Attendance).values(
                    tenant_id=t_id,
                    employee_id=emp.id,
                    attendance_date=d,
                    status=target_status,
                    source=target_source,
                    leave_request_id=target_leave_id,
                    locked=False,
                    updated_by=actor_uuid,
                )
                if overwrite_existing:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["tenant_id", "employee_id", "attendance_date"],
                        set_={
                            "status": target_status,
                            "source": target_source,
                            "leave_request_id": target_leave_id,
                            "updated_by": actor_uuid,
                            "updated_at": datetime.now(UTC),
                        },
                        where=Attendance.locked.is_(False),
                    )
                else:
                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=["tenant_id", "employee_id", "attendance_date"]
                    )
                await db.execute(stmt)

        total_filled = filled_leave + filled_holiday + filled_weekend
        await write_audit(
            session=db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="attendance.month_auto_filled",
            target_type="attendance_month",
            target_id=year_month,
            details={
                "year_month": year_month,
                "active_employees": len(employees),
                "filled_leave_days": filled_leave,
                "filled_holiday_days": filled_holiday,
                "filled_weekend_days": filled_weekend,
                "total_filled": total_filled,
                "overwrite_existing": overwrite_existing,
            },
        )

        active_count = len(employees)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)

        return AttendanceAutoFillResponse(
            year_month=year_month,
            active_employees=active_count,
            filled_leave_days=filled_leave,
            filled_holiday_days=filled_holiday,
            filled_weekend_days=filled_weekend,
            total_filled=total_filled,
            skipped_existing=skipped_existing,
        )

    @classmethod
    async def _compute_month_review(
        cls,
        db: AsyncSession,
        *,
        tenant_id: UUID,
        year_month: str,
        actor_id: UUID | None = None,
    ) -> AttendanceMonthReviewResponse:
        yr, _mo, month_start, month_end = validate_year_month(year_month)
        lock_row = await _get_month_lock(db, tenant_id, year_month)

        await LeaveConfigService.get_or_create_work_week(db, tenant_id, actor_id=actor_id)
        ww = (
            await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == tenant_id))
        ).scalar_one()
        weekend_set = {d.strip().lower() for d in (ww.weekend_days or ["Saturday", "Sunday"])}

        await LeaveConfigService.ensure_default_pk_holidays(db, tenant_id, year=yr)
        holiday_dates = set(
            (
                await db.execute(
                    select(Holiday.holiday_date).where(
                        Holiday.tenant_id == tenant_id,
                        Holiday.is_optional.is_(False),
                        Holiday.holiday_date >= month_start,
                        Holiday.holiday_date <= month_end,
                    )
                )
            )
            .scalars()
            .all()
        )

        employees = await _load_active_employees_for_month(db, tenant_id, month_end)
        dept_names = await _load_department_names(db, tenant_id)
        leave_map = await _load_approved_leave_map(
            db, tenant_id=tenant_id, month_start=month_start, month_end=month_end
        )

        att_rows = (
            (
                await db.execute(
                    select(Attendance).where(
                        Attendance.tenant_id == tenant_id,
                        Attendance.attendance_date >= month_start,
                        Attendance.attendance_date <= month_end,
                    )
                )
            )
            .scalars()
            .all()
        )
        att_by_emp_date: dict[tuple[UUID, date], str] = {
            (r.employee_id, r.attendance_date): r.status for r in att_rows
        }

        all_days = [
            date.fromordinal(month_start.toordinal() + offset)
            for offset in range((month_end - month_start).days + 1)
        ]

        emp_items: list[EmployeeMonthGapItem] = []
        total_gaps = 0
        auto_fillable_gaps = 0

        for emp in employees:
            counts = {
                "PRESENT": 0,
                "WFH": 0,
                "HALF_DAY": 0,
                "ON_LEAVE": 0,
                "ABSENT": 0,
                "HOLIDAY": 0,
                "WEEKEND": 0,
            }
            missing_days: list[date] = []
            for d in all_days:
                st = att_by_emp_date.get((emp.id, d))
                if st is None:
                    missing_days.append(d)
                    is_wknd = d.strftime("%A").lower() in weekend_set
                    is_hol = d in holiday_dates
                    if is_wknd or is_hol or (emp.id, d) in leave_map:
                        auto_fillable_gaps += 1
                elif st in counts:
                    counts[st] += 1

            total_gaps += len(missing_days)
            emp_items.append(
                EmployeeMonthGapItem(
                    employee_id=emp.id,
                    employee_code=emp.employee_code,
                    employee_name=emp.full_name,
                    employee_email=emp.email,
                    department_name=dept_names.get(emp.department_id)
                    if emp.department_id
                    else None,
                    present_days=counts["PRESENT"],
                    wfh_days=counts["WFH"],
                    half_days=counts["HALF_DAY"],
                    on_leave_days=counts["ON_LEAVE"],
                    absent_days=counts["ABSENT"],
                    holiday_days=counts["HOLIDAY"],
                    weekend_days=counts["WEEKEND"],
                    recorded_days=len(all_days) - len(missing_days),
                    missing_days=missing_days,
                    missing_count=len(missing_days),
                )
            )

        is_locked = lock_row is not None
        return AttendanceMonthReviewResponse(
            year_month=year_month,
            month_start=month_start,
            month_end=month_end,
            days_in_month=len(all_days),
            active_employees=len(emp_items),
            total_gaps=total_gaps,
            auto_fillable_gaps=auto_fillable_gaps,
            is_locked=is_locked,
            can_lock=(total_gaps == 0 and not is_locked and len(emp_items) > 0),
            locked_at=lock_row.locked_at if lock_row else None,
            locked_by=lock_row.locked_by if lock_row else None,
            employees=emp_items,
        )

    @classmethod
    async def get_month_review(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        year_month: str,
    ) -> AttendanceMonthReviewResponse:
        """Returns monthly completeness review and exact missing dates per active employee."""
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        review = await cls._compute_month_review(
            db, tenant_id=t_id, year_month=year_month, actor_id=actor_uuid
        )
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)
        return review

    @classmethod
    async def lock_month(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        year_month: str,
    ) -> AttendanceMonthLockResponse:
        """
        Server-side gap verification & atomic month lock:
        - Refuses with HTTP 409 `ATTENDANCE_MONTH_LOCKED` if already locked.
        - Refuses with HTTP 409 `ATTENDANCE_GAPS_EXIST` if any active employee has missing days.
        - Sets `attendance.locked = True`, creates `attendance_month_locks`, emits
          `attendance.month_locked` outbox event, queues Sheets sync, and audits.
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        _yr, _mo, month_start, month_end = validate_year_month(year_month)

        existing_lock = await _get_month_lock(db, t_id, year_month)
        if existing_lock is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ATTENDANCE_MONTH_LOCKED: Attendance month '{year_month}' is already locked.",
            )

        review = await cls._compute_month_review(
            db, tenant_id=t_id, year_month=year_month, actor_id=actor_uuid
        )
        if review.active_employees == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ATTENDANCE_GAPS_EXIST: No active employees eligible for '{year_month}'.",
            )
        if review.total_gaps > 0:
            samples: list[str] = []
            for emp_item in review.employees:
                for missing_d in emp_item.missing_days:
                    samples.append(f"{emp_item.employee_email}:{missing_d.isoformat()}")
                    if len(samples) >= 5:
                        break
                if len(samples) >= 5:
                    break
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"ATTENDANCE_GAPS_EXIST: Cannot lock '{year_month}' because "
                    f"{review.total_gaps} attendance gap(s) remain. "
                    f"Sample gaps: {', '.join(samples)}"
                ),
            )

        await db.execute(
            update(Attendance)
            .where(
                Attendance.tenant_id == t_id,
                Attendance.attendance_date >= month_start,
                Attendance.attendance_date <= month_end,
            )
            .values(locked=True, updated_by=actor_uuid, updated_at=datetime.now(UTC))
        )

        record_count = sum(e.recorded_days for e in review.employees)
        now_utc = datetime.now(UTC)
        lock_row = AttendanceMonthLock(
            tenant_id=t_id,
            year_month=year_month,
            locked_by=actor_uuid,
            locked_at=now_utc,
            employee_count=review.active_employees,
            record_count=record_count,
        )
        db.add(lock_row)
        await db.flush()

        await enqueue_outbox_event(
            session=db,
            tenant_id=t_id,
            event_type="attendance.month_locked",
            event_payload={
                "tenant_id": str(t_id),
                "year_month": year_month,
                "locked_by": str(actor_uuid),
                "employee_count": review.active_employees,
                "record_count": record_count,
            },
        )
        await SheetsSyncService.enqueue_attendance_sync(
            session=db,
            tenant_id=t_id,
            entity_id=year_month,
            action="month_lock",
            entity_type="attendance_month_lock",
            payload={
                "year_month": year_month,
                "employee_count": review.active_employees,
                "record_count": record_count,
                "status": "LOCKED",
                "source": "month_lock",
                "locked": True,
            },
        )
        await write_audit(
            session=db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="attendance.month_locked",
            target_type="attendance_month_lock",
            target_id=lock_row.id,
            details={
                "year_month": year_month,
                "employee_count": review.active_employees,
                "record_count": record_count,
            },
        )

        resp = AttendanceMonthLockResponse(
            id=lock_row.id,
            tenant_id=lock_row.tenant_id,
            year_month=lock_row.year_month,
            locked_by=lock_row.locked_by,
            locked_at=lock_row.locked_at,
            employee_count=lock_row.employee_count,
            record_count=lock_row.record_count,
        )
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)
        return resp

    @classmethod
    async def request_post_lock_edit(
        cls,
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        payload: AttendancePostLockEditRequest,
    ) -> AttendancePostLockEditResponse:
        """
        Initiates a Stage 2 governance approval (`attendance.post_lock_edit`) to modify a
        locked attendance month. Approving it applies the change atomically in `approval_service.py`.
        """
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        ym = payload.attendance_date.strftime("%Y-%m")

        lock_row = await _get_month_lock(db, t_id, ym)
        if lock_row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"ATTENDANCE_MONTH_NOT_LOCKED: Month '{ym}' is not locked; "
                    "edit attendance directly via the daily grid."
                ),
            )

        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == t_id,
                    Employee.id == payload.employee_id,
                )
            )
        ).scalar_one_or_none()
        if emp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee '{payload.employee_id}' not found in tenant.",
            )

        existing_att = (
            await db.execute(
                select(Attendance).where(
                    Attendance.tenant_id == t_id,
                    Attendance.employee_id == emp.id,
                    Attendance.attendance_date == payload.attendance_date,
                )
            )
        ).scalar_one_or_none()
        prev_status = existing_att.status if existing_att is not None else None
        emp_name = emp.full_name

        approval = await create_approval(
            db,
            actor,
            ApprovalCreateRequest(
                approval_type="attendance.post_lock_edit",
                approver_role=payload.approver_role,
                payload={
                    "employee_id": str(emp.id),
                    "employee_name": emp_name,
                    "attendance_date": payload.attendance_date.isoformat(),
                    "year_month": ym,
                    "previous_status": prev_status,
                    "new_status": payload.new_status,
                    "notes": payload.notes,
                    "reason": payload.reason,
                },
            ),
            commit=False,
        )

        approval_id = approval.id
        approval_status = approval.status
        approver_role = approval.approver_role

        await write_audit(
            session=db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="attendance.post_lock_edit_requested",
            target_type="approval",
            target_id=approval_id,
            details={
                "employee_id": str(emp.id),
                "attendance_date": payload.attendance_date.isoformat(),
                "year_month": ym,
                "previous_status": prev_status,
                "new_status": payload.new_status,
                "reason": payload.reason,
            },
        )

        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.user_id)

        return AttendancePostLockEditResponse(
            approval_id=approval_id,
            approval_status=approval_status,
            approver_role=approver_role,
            employee_id=payload.employee_id,
            employee_name=emp_name,
            attendance_date=payload.attendance_date,
            year_month=ym,
            previous_status=prev_status,  # type: ignore[arg-type]
            new_status=payload.new_status,
            notes=payload.notes,
            reason=payload.reason,
        )
