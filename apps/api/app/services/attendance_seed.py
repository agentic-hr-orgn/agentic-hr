"""
3-Month Deterministic Attendance Seed Service for Agentic HR Core (Stage 6 Prompt 4).

Deterministically populates 3 full calendar months of realistic attendance (`PRESENT`, `WFH`,
`HALF_DAY`, `WEEKEND`, `HOLIDAY`, `ON_LEAVE`) for active tenant employees so `pub_attendance`,
Month Review, and HR dashboards have rich multi-month data without gaps.
"""

from __future__ import annotations

import calendar
import hashlib
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezones import KARACHI_TZ_NAME, get_karachi_tz
from app.models.attendance import Attendance
from app.models.employee import Employee
from app.models.leave import Holiday, LeaveRequest, WorkWeek
from app.services.leave_config_service import LeaveConfigService


def _shift_year_month(year: int, month: int, delta_months: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) + delta_months
    return total // 12, (total % 12) + 1


def _deterministic_bucket(employee_id: UUID, target_date: date) -> int:
    digest = hashlib.sha256(f"{employee_id}:{target_date.isoformat()}".encode()).digest()
    return int.from_bytes(digest[:2], "big") % 100


async def seed_three_months_attendance(
    db: AsyncSession,
    *,
    tenant_id: UUID | str,
    reference_date: date | None = None,
    actor_id: UUID | str | None = None,
    year_months: list[str] | None = None,
) -> dict[str, Any]:
    """
    Deterministically seeds 3 full calendar months of attendance for active tenant employees.
    Idempotent via `ON CONFLICT (tenant_id, employee_id, attendance_date) DO NOTHING`.
    """
    t_id = UUID(str(tenant_id))
    act_uuid = UUID(str(actor_id)) if actor_id else None

    await LeaveConfigService.get_or_create_work_week(db, t_id, actor_id=act_uuid)
    ww = (await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id))).scalar_one()
    pkt_tz = get_karachi_tz(ww.timezone or KARACHI_TZ_NAME)
    weekend_set = {d.strip().lower() for d in (ww.weekend_days or ["Saturday", "Sunday"])}

    ref_d = reference_date or datetime.now(pkt_tz).date()
    if year_months:
        target_yms = list(year_months)
    else:
        target_yms = []
        for delta in (-2, -1, 0):
            y, m = _shift_year_month(ref_d.year, ref_d.month, delta)
            target_yms.append(f"{y:04d}-{m:02d}")

    years_covered = sorted({int(ym.split("-")[0]) for ym in target_yms})
    for yr in years_covered:
        await LeaveConfigService.ensure_default_pk_holidays(db, t_id, year=yr)

    first_y, first_m = map(int, target_yms[0].split("-"))
    last_y, last_m = map(int, target_yms[-1].split("-"))
    range_start = date(first_y, first_m, 1)
    range_end = date(last_y, last_m, calendar.monthrange(last_y, last_m)[1])

    holiday_dates = set(
        (
            await db.execute(
                select(Holiday.holiday_date).where(
                    Holiday.tenant_id == t_id,
                    Holiday.is_optional.is_(False),
                    Holiday.holiday_date >= range_start,
                    Holiday.holiday_date <= range_end,
                )
            )
        )
        .scalars()
        .all()
    )

    employees = list(
        (
            await db.execute(
                select(Employee)
                .where(
                    Employee.tenant_id == t_id,
                    Employee.status == "active",
                    or_(Employee.join_date.is_(None), Employee.join_date <= range_end),
                )
                .order_by(Employee.full_name.asc())
            )
        )
        .scalars()
        .all()
    )

    approved_leaves = (
        await db.execute(
            select(
                LeaveRequest.id,
                LeaveRequest.employee_id,
                LeaveRequest.start_date,
                LeaveRequest.end_date,
                LeaveRequest.is_half_day,
            ).where(
                LeaveRequest.tenant_id == t_id,
                LeaveRequest.status == "approved",
                LeaveRequest.start_date <= range_end,
                LeaveRequest.end_date >= range_start,
            )
        )
    ).all()
    leave_map: dict[tuple[UUID, date], tuple[UUID, str]] = {}
    for req_id, emp_id, s_d, e_d, is_half in approved_leaves:
        curr = max(s_d, range_start)
        end_c = min(e_d, range_end)
        while curr <= end_c:
            leave_map[(emp_id, curr)] = (req_id, "HALF_DAY" if is_half else "ON_LEAVE")
            curr = date.fromordinal(curr.toordinal() + 1)

    inserted_rows = 0
    for ym in target_yms:
        y, m = map(int, ym.split("-"))
        days_in_m = calendar.monthrange(y, m)[1]
        m_start = date(y, m, 1)
        m_end = date(y, m, days_in_m)

        for emp in employees:
            if emp.join_date is not None and emp.join_date > m_end:
                continue
            for day_num in range(1, days_in_m + 1):
                d = date(y, m, day_num)
                if d < m_start:
                    continue

                is_weekend = d.strftime("%A").lower() in weekend_set
                is_holiday = d in holiday_dates
                leave_entry = leave_map.get((emp.id, d))

                check_in_at: datetime | None = None
                check_out_at: datetime | None = None
                leave_req_id: UUID | None = None

                if leave_entry is not None and not is_weekend and not is_holiday:
                    leave_req_id, att_status = leave_entry
                    att_source = "leave_approval"
                elif is_holiday:
                    att_status = "HOLIDAY"
                    att_source = "auto_fill"
                elif is_weekend:
                    att_status = "WEEKEND"
                    att_source = "auto_fill"
                else:
                    bucket = _deterministic_bucket(emp.id, d)
                    if bucket < 75:
                        att_status = "PRESENT"
                        att_source = "check_in"
                        check_in_at = datetime.combine(d, time(9, 0), tzinfo=pkt_tz)
                        check_out_at = datetime.combine(d, time(17, 30), tzinfo=pkt_tz)
                    elif bucket < 92:
                        att_status = "WFH"
                        att_source = "manager_grid"
                    else:
                        att_status = "HALF_DAY"
                        att_source = "manager_grid"

                res = await db.execute(
                    pg_insert(Attendance)
                    .values(
                        tenant_id=t_id,
                        employee_id=emp.id,
                        attendance_date=d,
                        status=att_status,
                        source=att_source,
                        check_in_at=check_in_at,
                        check_out_at=check_out_at,
                        leave_request_id=leave_req_id,
                        locked=False,
                        updated_by=act_uuid,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["tenant_id", "employee_id", "attendance_date"]
                    )
                    .returning(Attendance.id)
                )
                if res.scalar_one_or_none() is not None:
                    inserted_rows += 1

    await db.flush()
    return {
        "year_months": target_yms,
        "employees_processed": len(employees),
        "inserted_rows": inserted_rows,
    }
