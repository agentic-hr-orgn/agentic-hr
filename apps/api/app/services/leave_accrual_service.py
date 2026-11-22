"""
Leave Monthly Accrual & Year-End Carry-Forward Engine (Stage 6 Prompt 2).

Complies with ADR 009:
- Row-level pessimistic locking (`SELECT ... FOR UPDATE`) on `leave_balances`.
- Immutable append-only `leave_ledger` double-entry accounting with deterministic
  `idempotency_key` deduplication (`ON CONFLICT DO NOTHING RETURNING id`) so re-runs
  and Temporal retries never double-credit.
- Mutually idempotent `init-balance:{employee_id}:{leave_type_id}:{to_year}` key shared
  with Prompt 1's `initialize_employee_leave_balances()`.
- Carry-forward credits record `delta_allocated = carry_days` on `entry_type='carry_forward'`
  so `SUM(delta_allocated) == allocated_days + carried_forward_days`.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.leave import LeaveBalance, LeaveLedger, LeaveType
from app.services.audit_service import write_audit

YEAR_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
ELIGIBLE_EMPLOYEE_STATUSES: tuple[str, ...] = ("active", "on_leave")


def _parse_year_month(year_month: str) -> tuple[int, int, date]:
    match = YEAR_MONTH_RE.match((year_month or "").strip())
    if not match:
        raise ValueError(f"Invalid year_month '{year_month}'. Expected YYYY-MM.")
    year = int(match.group(1))
    month = int(match.group(2))
    last_day = calendar.monthrange(year, month)[1]
    return year, month, date(year, month, last_day)


async def run_monthly_accrual(
    db: AsyncSession,
    tenant_id: UUID | str,
    year_month: str,
    actor_id: UUID | str | None = None,
) -> dict[str, Any]:
    """
    Credits `annual_allowance_days / 12` (2-decimal `ROUND_HALF_UP`) for all active
    `accrual_mode == 'monthly'` leave types across active/on_leave tenant employees
    whose `join_date` is either unset or on/before the end of `year_month`.

    Strictly idempotent via `idempotency_key = f"accrual:{employee_id}:{leave_type_id}:{year_month}"`.
    Does not commit; caller (Temporal activity or service) controls transaction commit.
    """
    t_id = UUID(str(tenant_id))
    actor_uuid = UUID(str(actor_id)) if actor_id else None
    year, _month, month_end = _parse_year_month(year_month)

    monthly_types = list(
        (
            await db.execute(
                select(LeaveType)
                .where(
                    LeaveType.tenant_id == t_id,
                    LeaveType.is_active.is_(True),
                    LeaveType.accrual_mode == "monthly",
                    LeaveType.annual_allowance_days > Decimal("0.00"),
                )
                .order_by(LeaveType.id.asc())
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
                    Employee.status.in_(ELIGIBLE_EMPLOYEE_STATUSES),
                )
                .order_by(Employee.id.asc())
            )
        )
        .scalars()
        .all()
    )

    eligible_employees = [
        emp for emp in employees if emp.join_date is None or emp.join_date <= month_end
    ]

    credited_count = 0
    skipped_count = 0
    total_days_credited = Decimal("0.00")

    for emp in eligible_employees:
        for lt in monthly_types:
            monthly_credit = (Decimal(str(lt.annual_allowance_days)) / Decimal(12)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if monthly_credit <= Decimal("0.00"):
                continue

            # Ensure balance row exists before acquiring FOR UPDATE lock
            await db.execute(
                pg_insert(LeaveBalance)
                .values(
                    tenant_id=t_id,
                    employee_id=emp.id,
                    leave_type_id=lt.id,
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
                        LeaveBalance.tenant_id == t_id,
                        LeaveBalance.employee_id == emp.id,
                        LeaveBalance.leave_type_id == lt.id,
                        LeaveBalance.year == year,
                    )
                    .with_for_update()
                )
            ).scalar_one()

            idem_key = f"accrual:{emp.id}:{lt.id}:{year_month}"
            inserted_ledger_id = (
                await db.execute(
                    pg_insert(LeaveLedger)
                    .values(
                        tenant_id=t_id,
                        employee_id=emp.id,
                        leave_type_id=lt.id,
                        balance_id=bal.id,
                        year=year,
                        entry_type="accrual",
                        delta_allocated=monthly_credit,
                        delta_reserved=Decimal("0.00"),
                        delta_used=Decimal("0.00"),
                        reference_type="monthly_accrual",
                        reference_id=None,
                        note=f"Monthly accrual for {year_month} ({lt.code})",
                        actor_id=actor_uuid,
                        idempotency_key=idem_key,
                    )
                    .on_conflict_do_nothing()
                    .returning(LeaveLedger.id)
                )
            ).scalar_one_or_none()

            if inserted_ledger_id is None:
                skipped_count += 1
                continue

            bal.allocated_days = (Decimal(str(bal.allocated_days)) + monthly_credit).quantize(
                Decimal("0.01")
            )
            credited_count += 1
            total_days_credited += monthly_credit

    await db.flush()

    summary: dict[str, Any] = {
        "tenant_id": str(t_id),
        "year_month": year_month,
        "employees_processed": len(eligible_employees),
        "leave_types_processed": len(monthly_types),
        "credited_count": credited_count,
        "skipped_count": skipped_count,
        "total_days_credited": str(total_days_credited.quantize(Decimal("0.01"))),
    }

    await write_audit(
        db,
        tenant_id=t_id,
        actor_id=actor_uuid,
        action="leave.monthly_accrual_completed",
        target_type="leave_accrual",
        target_id=year_month,
        details=summary,
    )
    return summary


async def run_year_end_carry_forward(
    db: AsyncSession,
    tenant_id: UUID | str,
    from_year: int,
    to_year: int,
    actor_id: UUID | str | None = None,
) -> dict[str, Any]:
    """
    Rolls balances from `from_year` to `to_year`:
    1. Idempotently initializes `to_year` balances using the shared
       `init-balance:{employee_id}:{leave_type_id}:{to_year}` key (`upfront` credits
       `annual_allowance_days`; `monthly` initializes `0.00`).
    2. Computes remaining `from_year` balance capped at `leave_type.carry_forward_cap_days`,
       writes an idempotent `entry_type='carry_forward'` ledger row with
       `delta_allocated = carry_days`, and updates `to_year.carried_forward_days`.

    Does not commit; caller controls transaction commit.
    """
    if to_year != from_year + 1:
        raise ValueError(
            f"Invalid carry-forward years ({from_year} -> {to_year}): to_year must equal from_year + 1."
        )
    if from_year < 2000 or to_year > 2100:
        raise ValueError(f"Years ({from_year} -> {to_year}) out of supported range 2000..2100.")

    t_id = UUID(str(tenant_id))
    actor_uuid = UUID(str(actor_id)) if actor_id else None

    active_types = list(
        (
            await db.execute(
                select(LeaveType)
                .where(LeaveType.tenant_id == t_id, LeaveType.is_active.is_(True))
                .order_by(LeaveType.id.asc())
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
                    Employee.status.in_(ELIGIBLE_EMPLOYEE_STATUSES),
                )
                .order_by(Employee.id.asc())
            )
        )
        .scalars()
        .all()
    )

    initialized_count = 0
    carried_count = 0
    skipped_count = 0
    total_days_carried = Decimal("0.00")

    for emp in employees:
        for lt in active_types:
            # Step 1: Ensure to_year balance exists and lock it FOR UPDATE
            await db.execute(
                pg_insert(LeaveBalance)
                .values(
                    tenant_id=t_id,
                    employee_id=emp.id,
                    leave_type_id=lt.id,
                    year=to_year,
                    allocated_days=Decimal("0.00"),
                    carried_forward_days=Decimal("0.00"),
                    reserved_days=Decimal("0.00"),
                    used_days=Decimal("0.00"),
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "employee_id", "leave_type_id", "year"]
                )
            )
            bal_to = (
                await db.execute(
                    select(LeaveBalance)
                    .where(
                        LeaveBalance.tenant_id == t_id,
                        LeaveBalance.employee_id == emp.id,
                        LeaveBalance.leave_type_id == lt.id,
                        LeaveBalance.year == to_year,
                    )
                    .with_for_update()
                )
            ).scalar_one()

            # Shared idempotency key with initialize_employee_leave_balances()
            initial_alloc = (
                Decimal(str(lt.annual_allowance_days)).quantize(Decimal("0.01"))
                if lt.accrual_mode == "upfront"
                else Decimal("0.00")
            )
            init_key = f"init-balance:{emp.id}:{lt.id}:{to_year}"
            init_ledger_id = (
                await db.execute(
                    pg_insert(LeaveLedger)
                    .values(
                        tenant_id=t_id,
                        employee_id=emp.id,
                        leave_type_id=lt.id,
                        balance_id=bal_to.id,
                        year=to_year,
                        entry_type="accrual",
                        delta_allocated=initial_alloc,
                        delta_reserved=Decimal("0.00"),
                        delta_used=Decimal("0.00"),
                        reference_type="year_end_carry_forward",
                        reference_id=None,
                        note=f"Initial {to_year} {lt.code} balance ({lt.accrual_mode} accrual)",
                        actor_id=actor_uuid,
                        idempotency_key=init_key,
                    )
                    .on_conflict_do_nothing()
                    .returning(LeaveLedger.id)
                )
            ).scalar_one_or_none()

            if init_ledger_id is not None:
                bal_to.allocated_days = (
                    Decimal(str(bal_to.allocated_days)) + initial_alloc
                ).quantize(Decimal("0.01"))
                initialized_count += 1

            # Step 2: Carry forward capped remaining balance from `from_year`
            cap = Decimal(str(lt.carry_forward_cap_days)).quantize(Decimal("0.01"))
            if cap <= Decimal("0.00"):
                continue

            bal_from = (
                await db.execute(
                    select(LeaveBalance).where(
                        LeaveBalance.tenant_id == t_id,
                        LeaveBalance.employee_id == emp.id,
                        LeaveBalance.leave_type_id == lt.id,
                        LeaveBalance.year == from_year,
                    )
                )
            ).scalar_one_or_none()
            if bal_from is None:
                continue

            remaining = (
                Decimal(str(bal_from.allocated_days))
                + Decimal(str(bal_from.carried_forward_days))
                - Decimal(str(bal_from.reserved_days))
                - Decimal(str(bal_from.used_days))
            )
            if remaining <= Decimal("0.00"):
                continue

            carry_days = min(remaining, cap).quantize(Decimal("0.01"))
            cf_key = f"carry-forward:{emp.id}:{lt.id}:{from_year}:{to_year}"
            cf_ledger_id = (
                await db.execute(
                    pg_insert(LeaveLedger)
                    .values(
                        tenant_id=t_id,
                        employee_id=emp.id,
                        leave_type_id=lt.id,
                        balance_id=bal_to.id,
                        year=to_year,
                        entry_type="carry_forward",
                        delta_allocated=carry_days,
                        delta_reserved=Decimal("0.00"),
                        delta_used=Decimal("0.00"),
                        reference_type="year_end_carry_forward",
                        reference_id=bal_from.id,
                        note=f"Carry forward from {from_year} to {to_year} (cap {cap})",
                        actor_id=actor_uuid,
                        idempotency_key=cf_key,
                    )
                    .on_conflict_do_nothing()
                    .returning(LeaveLedger.id)
                )
            ).scalar_one_or_none()

            if cf_ledger_id is None:
                skipped_count += 1
                continue

            bal_to.carried_forward_days = (
                Decimal(str(bal_to.carried_forward_days)) + carry_days
            ).quantize(Decimal("0.01"))
            carried_count += 1
            total_days_carried += carry_days

    await db.flush()

    summary: dict[str, Any] = {
        "tenant_id": str(t_id),
        "from_year": from_year,
        "to_year": to_year,
        "employees_processed": len(employees),
        "initialized_count": initialized_count,
        "carried_count": carried_count,
        "skipped_count": skipped_count,
        "total_days_carried": str(total_days_carried.quantize(Decimal("0.01"))),
    }

    await write_audit(
        db,
        tenant_id=t_id,
        actor_id=actor_uuid,
        action="leave.year_end_carry_forward_completed",
        target_type="leave_carry_forward",
        target_id=f"{from_year}->{to_year}",
        details=summary,
    )
    return summary
