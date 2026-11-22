"""
Leave & Attendance Configuration Service + Idempotent Balance Initialization (Stage 6 Prompt 1).

Provides:
- Race-safe on-demand seeding (`ON CONFLICT DO NOTHING`) for Pakistani SME default leave types,
  work_week settings, and fixed-date Pakistan public holidays.
- Bidirectional synchronization with Onboarding Wizard Step 3 (`structure_config`).
- `initialize_employee_leave_balances(db, tenant_id, employee_id, year, actor_id)` creating
  `leave_balances` + immutable `leave_ledger` (`entry_type='accrual'`) rows atomically.
"""

from __future__ import annotations

import copy
import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezones import KARACHI_TZ_NAME, to_karachi
from app.models.attendance import Attendance
from app.models.leave import (
    Holiday,
    LeaveBalance,
    LeaveLedger,
    LeaveRequest,
    LeaveType,
    WorkWeek,
)
from app.models.membership import Membership
from app.models.onboarding_state import OnboardingState
from app.schemas.leave_config import (
    VALID_WEEKDAYS,
    HolidayCreateRequest,
    HolidayDeleteResponse,
    HolidayResponse,
    HolidayUpdateRequest,
    LeaveTypeCreateRequest,
    LeaveTypeDeleteResponse,
    LeaveTypeResponse,
    LeaveTypeUpdateRequest,
    WorkWeekResponse,
    WorkWeekUpdateRequest,
)
from app.services.audit_service import write_audit

logger = logging.getLogger("agentic_hr.services.leave_config")

DEFAULT_PK_LEAVE_TYPES: tuple[dict[str, Any], ...] = (
    {
        "code": "annual",
        "name": "Annual Leave",
        "annual_allowance_days": Decimal("20.00"),
        "accrual_mode": "upfront",
        "carry_forward_cap_days": Decimal("5.00"),
        "max_consecutive_days": None,
        "min_notice_days": 3,
        "allow_during_probation": False,
        "requires_document_after_days": None,
        "allow_half_day": True,
        "is_paid": True,
        "is_active": True,
    },
    {
        "code": "sick",
        "name": "Sick Leave",
        "annual_allowance_days": Decimal("10.00"),
        "accrual_mode": "upfront",
        "carry_forward_cap_days": Decimal("0.00"),
        "max_consecutive_days": None,
        "min_notice_days": 0,
        "allow_during_probation": True,
        "requires_document_after_days": 2,
        "allow_half_day": True,
        "is_paid": True,
        "is_active": True,
    },
    {
        "code": "casual",
        "name": "Casual Leave",
        "annual_allowance_days": Decimal("10.00"),
        "accrual_mode": "upfront",
        "carry_forward_cap_days": Decimal("0.00"),
        "max_consecutive_days": 3,
        "min_notice_days": 1,
        "allow_during_probation": True,
        "requires_document_after_days": None,
        "allow_half_day": True,
        "is_paid": True,
        "is_active": True,
    },
    {
        "code": "unpaid",
        "name": "Unpaid Leave",
        "annual_allowance_days": Decimal("0.00"),
        "accrual_mode": "upfront",
        "carry_forward_cap_days": Decimal("0.00"),
        "max_consecutive_days": None,
        "min_notice_days": 0,
        "allow_during_probation": True,
        "requires_document_after_days": None,
        "allow_half_day": True,
        "is_paid": False,
        "is_active": True,
    },
)

DEFAULT_PK_FIXED_HOLIDAYS: tuple[tuple[int, int, str], ...] = (
    (2, 5, "Kashmir Solidarity Day"),
    (3, 23, "Pakistan Day"),
    (5, 1, "Labour Day"),
    (8, 14, "Independence Day"),
    (11, 9, "Iqbal Day"),
    (12, 25, "Quaid-e-Azam Day"),
)

_WEEKDAY_MAP: dict[str, str] = {day.lower(): day for day in VALID_WEEKDAYS}


def _current_pkt_year() -> int:
    return to_karachi(datetime.now(UTC)).year


def _normalize_weekends(raw_weekends: Any) -> list[str]:
    if not isinstance(raw_weekends, list):
        return ["Saturday", "Sunday"]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_weekends:
        canonical = _WEEKDAY_MAP.get(str(item).strip().lower())
        if canonical and canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out or ["Saturday", "Sunday"]


async def initialize_employee_leave_balances(
    db: AsyncSession,
    tenant_id: UUID | str,
    employee_id: UUID | str,
    year: int | None = None,
    actor_id: UUID | str | None = None,
) -> int:
    """
    Idempotently initializes `leave_balances` for every active `leave_type` in the tenant
    and appends an immutable `leave_ledger` (`entry_type='accrual'`) row for each newly
    created balance inside the caller's active transaction.

    - `accrual_mode == 'upfront'`: credits `allocated_days = lt.annual_allowance_days`.
    - `accrual_mode == 'monthly'`: initializes `allocated_days = Decimal('0.00')` (with a
      `0.00` initial ledger entry) so scheduled monthly accrual jobs never double-allocate.

    Returns the number of newly created balance rows.
    """
    t_id = UUID(str(tenant_id))
    emp_id = UUID(str(employee_id))
    target_year = year if year is not None else _current_pkt_year()
    actor_uuid = UUID(str(actor_id)) if actor_id else None

    await LeaveConfigService.ensure_default_leave_types(db, t_id, actor_id=actor_uuid)

    active_types = (
        (
            await db.execute(
                select(LeaveType)
                .where(LeaveType.tenant_id == t_id, LeaveType.is_active.is_(True))
                .order_by(LeaveType.code.asc())
            )
        )
        .scalars()
        .all()
    )
    if not active_types:
        return 0

    created_count = 0
    for lt in active_types:
        initial_allocated = (
            Decimal(str(lt.annual_allowance_days))
            if lt.accrual_mode == "upfront"
            else Decimal("0.00")
        )
        bal_stmt = (
            pg_insert(LeaveBalance)
            .values(
                tenant_id=t_id,
                employee_id=emp_id,
                leave_type_id=lt.id,
                year=target_year,
                allocated_days=initial_allocated,
                carried_forward_days=Decimal("0.00"),
                reserved_days=Decimal("0.00"),
                used_days=Decimal("0.00"),
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "employee_id", "leave_type_id", "year"]
            )
            .returning(LeaveBalance.id)
        )
        inserted_balance_id = (await db.execute(bal_stmt)).scalar_one_or_none()
        if inserted_balance_id is None:
            continue

        idem_key = f"init-balance:{emp_id}:{lt.id}:{target_year}"
        ledger_stmt = (
            pg_insert(LeaveLedger)
            .values(
                tenant_id=t_id,
                employee_id=emp_id,
                leave_type_id=lt.id,
                balance_id=inserted_balance_id,
                year=target_year,
                entry_type="accrual",
                delta_allocated=initial_allocated,
                delta_reserved=Decimal("0.00"),
                delta_used=Decimal("0.00"),
                reference_type="employee_init",
                reference_id=emp_id,
                note=(f"Initial {target_year} {lt.code} balance ({lt.accrual_mode} accrual)"),
                actor_id=actor_uuid,
                idempotency_key=idem_key,
            )
            .on_conflict_do_nothing()
        )
        await db.execute(ledger_stmt)
        created_count += 1

    await db.flush()
    return created_count


class LeaveConfigService:
    """Tenant-scoped Leave & Work-Week configuration CRUD and seeding service."""

    @staticmethod
    async def ensure_default_leave_types(
        db: AsyncSession,
        tenant_id: UUID | str,
        actor_id: UUID | str | None = None,
    ) -> list[LeaveType]:
        """
        Seeds the 4 standard Pakistani SME leave types (`annual`, `sick`, `casual`, `unpaid`)
        when a tenant has no `leave_types` rows yet. Race-safe via `ON CONFLICT DO NOTHING`.
        """
        t_id = UUID(str(tenant_id))
        existing_count = int(
            (
                await db.execute(
                    select(func.count(LeaveType.id)).where(LeaveType.tenant_id == t_id)
                )
            ).scalar()
            or 0
        )
        if existing_count == 0:
            rows = [{"tenant_id": t_id, **spec} for spec in DEFAULT_PK_LEAVE_TYPES]
            stmt = (
                pg_insert(LeaveType)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["tenant_id", "code"])
            )
            await db.execute(stmt)
            await db.flush()

        return list(
            (
                await db.execute(
                    select(LeaveType)
                    .where(LeaveType.tenant_id == t_id)
                    .order_by(LeaveType.code.asc())
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def get_or_create_work_week(
        db: AsyncSession,
        tenant_id: UUID | str,
        actor_id: UUID | str | None = None,
    ) -> WorkWeekResponse:
        """
        Returns the tenant's single `work_week` row, creating it on demand with
        `onboarding_state.structure_config["weekends"]` if present. Race-safe.
        """
        t_id = UUID(str(tenant_id))
        row = (
            await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id))
        ).scalar_one_or_none()
        if row is not None:
            return WorkWeekResponse.model_validate(row)

        onb_state = (
            await db.execute(select(OnboardingState).where(OnboardingState.tenant_id == t_id))
        ).scalar_one_or_none()
        default_weekends = ["Saturday", "Sunday"]
        if onb_state and isinstance(onb_state.structure_config, dict):
            default_weekends = _normalize_weekends(onb_state.structure_config.get("weekends"))

        stmt = (
            pg_insert(WorkWeek)
            .values(
                tenant_id=t_id,
                weekend_days=default_weekends,
                probation_months=3,
                max_team_absent_pct=Decimal("30.00"),
                min_team_present=1,
                backup_approver_user_id=None,
                check_in_enabled=True,
                timezone=KARACHI_TZ_NAME,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id"])
        )
        await db.execute(stmt)
        await db.flush()

        created = (
            await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id))
        ).scalar_one()
        return WorkWeekResponse.model_validate(created)

    @classmethod
    async def update_work_week(
        cls,
        db: AsyncSession,
        tenant_id: UUID | str,
        payload: WorkWeekUpdateRequest,
        actor_id: UUID | str,
    ) -> WorkWeekResponse:
        """Updates tenant `work_week` settings, syncs onboarding weekends, and writes audit log."""
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id))
        await cls.get_or_create_work_week(db, t_id, actor_id=actor_uuid)

        row = (
            await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id).with_for_update())
        ).scalar_one()

        fields_set = payload.model_fields_set
        if "backup_approver_user_id" in fields_set and payload.backup_approver_user_id is not None:
            mem = (
                await db.execute(
                    select(Membership.id).where(
                        Membership.tenant_id == t_id,
                        Membership.user_id == payload.backup_approver_user_id,
                        Membership.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if mem is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="backup_approver_user_id must belong to an active member of this tenant.",
                )

        updated_fields: list[str] = []
        if "weekend_days" in fields_set and payload.weekend_days is not None:
            row.weekend_days = list(payload.weekend_days)
            updated_fields.append("weekend_days")
        if "probation_months" in fields_set and payload.probation_months is not None:
            row.probation_months = payload.probation_months
            updated_fields.append("probation_months")
        if "max_team_absent_pct" in fields_set and payload.max_team_absent_pct is not None:
            row.max_team_absent_pct = payload.max_team_absent_pct
            updated_fields.append("max_team_absent_pct")
        if "min_team_present" in fields_set and payload.min_team_present is not None:
            row.min_team_present = payload.min_team_present
            updated_fields.append("min_team_present")
        if "backup_approver_user_id" in fields_set:
            row.backup_approver_user_id = payload.backup_approver_user_id
            updated_fields.append("backup_approver_user_id")
        if "check_in_enabled" in fields_set and payload.check_in_enabled is not None:
            row.check_in_enabled = payload.check_in_enabled
            updated_fields.append("check_in_enabled")
        if "timezone" in fields_set and payload.timezone is not None:
            row.timezone = payload.timezone
            updated_fields.append("timezone")

        row.updated_at = datetime.now(UTC)
        await db.flush()

        # Sync weekend_days back to onboarding_state.structure_config if it exists
        if "weekend_days" in fields_set and payload.weekend_days is not None:
            onb_state = (
                await db.execute(select(OnboardingState).where(OnboardingState.tenant_id == t_id))
            ).scalar_one_or_none()
            if onb_state is not None:
                cfg = copy.deepcopy(onb_state.structure_config or {})
                cfg["weekends"] = list(row.weekend_days)
                onb_state.structure_config = cfg
                onb_state.updated_at = datetime.now(UTC)
                await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="leave_config.work_week_updated",
            target_type="work_week",
            target_id=row.id,
            details={"updated_fields": updated_fields, "weekend_days": list(row.weekend_days)},
        )
        return WorkWeekResponse.model_validate(row)

    @classmethod
    async def sync_from_onboarding_step3(
        cls,
        db: AsyncSession,
        tenant_id: UUID | str,
        structure_config: dict[str, Any] | None,
        actor_id: UUID | str | None = None,
    ) -> None:
        """
        Synchronizes Onboarding Wizard Step 3 `structure_config` (`weekends`, `leave_rules`,
        and optional `holidays`) into authoritative Stage 6 `work_week`, `leave_types`, and
        `holidays` tables inside the same DB transaction.
        """
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id)) if actor_id else None
        cfg = structure_config if isinstance(structure_config, dict) else {}

        await cls.get_or_create_work_week(db, t_id, actor_id=actor_uuid)
        ww = (
            await db.execute(select(WorkWeek).where(WorkWeek.tenant_id == t_id).with_for_update())
        ).scalar_one()

        if "weekends" in cfg:
            ww.weekend_days = _normalize_weekends(cfg.get("weekends"))
            ww.updated_at = datetime.now(UTC)

        await cls.ensure_default_leave_types(db, t_id, actor_id=actor_uuid)
        leave_rules = cfg.get("leave_rules")
        if isinstance(leave_rules, dict):
            code_map = {
                "annual_days": ("annual", "annual_allowance_days"),
                "sick_days": ("sick", "annual_allowance_days"),
                "casual_days": ("casual", "annual_allowance_days"),
                "carry_forward_max": ("annual", "carry_forward_cap_days"),
            }
            types_by_code = {
                lt.code: lt
                for lt in (await db.execute(select(LeaveType).where(LeaveType.tenant_id == t_id)))
                .scalars()
                .all()
            }
            for rule_key, (lt_code, col_name) in code_map.items():
                raw_val = leave_rules.get(rule_key)
                if raw_val is not None and lt_code in types_by_code:
                    try:
                        dec_val = Decimal(str(raw_val))
                    except (ValueError, TypeError, InvalidOperation) as exc:
                        logger.warning(
                            "Ignoring invalid onboarding leave_rules.%s=%r: %s",
                            rule_key,
                            raw_val,
                            exc,
                        )
                        continue
                    if dec_val >= Decimal("0.00"):
                        setattr(types_by_code[lt_code], col_name, dec_val)

        raw_holidays = cfg.get("holidays")
        if isinstance(raw_holidays, list):
            for item in raw_holidays:
                if not isinstance(item, dict):
                    continue
                raw_date = item.get("date") or item.get("holiday_date")
                raw_name = str(item.get("name") or "").strip()
                if not raw_date or not raw_name:
                    continue
                try:
                    parsed_date = (
                        raw_date
                        if isinstance(raw_date, date)
                        else date.fromisoformat(str(raw_date).strip()[:10])
                    )
                except ValueError:
                    continue
                stmt = (
                    pg_insert(Holiday)
                    .values(
                        tenant_id=t_id,
                        holiday_date=parsed_date,
                        name=raw_name[:200],
                        is_optional=bool(item.get("is_optional", False)),
                        source="custom",
                    )
                    .on_conflict_do_nothing(index_elements=["tenant_id", "holiday_date"])
                )
                await db.execute(stmt)

        await db.flush()

    @classmethod
    async def list_leave_types(
        cls,
        db: AsyncSession,
        tenant_id: UUID | str,
        *,
        include_inactive: bool = False,
    ) -> list[LeaveTypeResponse]:
        """Lists tenant leave types, auto-seeding defaults on first access."""
        t_id = UUID(str(tenant_id))
        await cls.ensure_default_leave_types(db, t_id)
        stmt = select(LeaveType).where(LeaveType.tenant_id == t_id)
        if not include_inactive:
            stmt = stmt.where(LeaveType.is_active.is_(True))
        stmt = stmt.order_by(LeaveType.code.asc())
        rows = (await db.execute(stmt)).scalars().all()
        return [LeaveTypeResponse.model_validate(r) for r in rows]

    @staticmethod
    async def create_leave_type(
        db: AsyncSession,
        tenant_id: UUID | str,
        payload: LeaveTypeCreateRequest,
        actor_id: UUID | str,
    ) -> LeaveTypeResponse:
        """Creates a new tenant leave type, rejecting duplicate codes with HTTP 409."""
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id))
        dup = (
            await db.execute(
                select(LeaveType.id).where(
                    LeaveType.tenant_id == t_id,
                    LeaveType.code == payload.code,
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"DUPLICATE_LEAVE_TYPE_CODE: Leave type '{payload.code}' already exists.",
            )

        lt = LeaveType(
            tenant_id=t_id,
            code=payload.code,
            name=payload.name,
            annual_allowance_days=payload.annual_allowance_days,
            accrual_mode=payload.accrual_mode,
            carry_forward_cap_days=payload.carry_forward_cap_days,
            max_consecutive_days=payload.max_consecutive_days,
            min_notice_days=payload.min_notice_days,
            allow_during_probation=payload.allow_during_probation,
            requires_document_after_days=payload.requires_document_after_days,
            allow_half_day=payload.allow_half_day,
            is_paid=payload.is_paid,
            is_active=payload.is_active,
        )
        db.add(lt)
        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="leave_config.leave_type_created",
            target_type="leave_type",
            target_id=lt.id,
            details={"code": lt.code, "name": lt.name, "accrual_mode": lt.accrual_mode},
        )
        return LeaveTypeResponse.model_validate(lt)

    @staticmethod
    async def update_leave_type(
        db: AsyncSession,
        tenant_id: UUID | str,
        leave_type_id: UUID,
        payload: LeaveTypeUpdateRequest,
        actor_id: UUID | str,
    ) -> LeaveTypeResponse:
        """Updates an existing leave type in the caller's tenant."""
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id))
        lt = (
            await db.execute(
                select(LeaveType).where(
                    LeaveType.tenant_id == t_id,
                    LeaveType.id == leave_type_id,
                )
            )
        ).scalar_one_or_none()
        if lt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Leave type '{leave_type_id}' not found.",
            )

        fields_set = payload.model_fields_set
        updated_fields: list[str] = []
        for field_name in (
            "name",
            "annual_allowance_days",
            "accrual_mode",
            "carry_forward_cap_days",
            "max_consecutive_days",
            "min_notice_days",
            "allow_during_probation",
            "requires_document_after_days",
            "allow_half_day",
            "is_paid",
            "is_active",
        ):
            if field_name in fields_set:
                val = getattr(payload, field_name)
                if (
                    field_name in ("max_consecutive_days", "requires_document_after_days")
                    or val is not None
                ):
                    setattr(lt, field_name, val)
                    updated_fields.append(field_name)

        lt.updated_at = datetime.now(UTC)
        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="leave_config.leave_type_updated",
            target_type="leave_type",
            target_id=lt.id,
            details={"code": lt.code, "updated_fields": updated_fields},
        )
        return LeaveTypeResponse.model_validate(lt)

    @staticmethod
    async def delete_leave_type(
        db: AsyncSession,
        tenant_id: UUID | str,
        leave_type_id: UUID,
        actor_id: UUID | str,
    ) -> LeaveTypeDeleteResponse:
        """
        Deletes a leave type if unreferenced, or safely soft-deactivates (`is_active=False`)
        if any balance, ledger row, or leave request references it.
        """
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id))
        lt = (
            await db.execute(
                select(LeaveType).where(
                    LeaveType.tenant_id == t_id,
                    LeaveType.id == leave_type_id,
                )
            )
        ).scalar_one_or_none()
        if lt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Leave type '{leave_type_id}' not found.",
            )

        has_bal = (
            await db.execute(
                select(LeaveBalance.id)
                .where(LeaveBalance.tenant_id == t_id, LeaveBalance.leave_type_id == lt.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        has_req = (
            await db.execute(
                select(LeaveRequest.id)
                .where(LeaveRequest.tenant_id == t_id, LeaveRequest.leave_type_id == lt.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        has_ledger = (
            await db.execute(
                select(LeaveLedger.id)
                .where(LeaveLedger.tenant_id == t_id, LeaveLedger.leave_type_id == lt.id)
                .limit(1)
            )
        ).scalar_one_or_none()

        code = lt.code
        if has_bal is not None or has_req is not None or has_ledger is not None:
            lt.is_active = False
            lt.updated_at = datetime.now(UTC)
            await db.flush()
            action = "deactivated"
            is_active = False
        else:
            await db.delete(lt)
            await db.flush()
            action = "deleted"
            is_active = False

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action=f"leave_config.leave_type_{action}",
            target_type="leave_type",
            target_id=leave_type_id,
            details={"code": code, "action": action},
        )
        return LeaveTypeDeleteResponse(
            id=leave_type_id,
            code=code,
            action=action,
            is_active=is_active,
        )

    @staticmethod
    async def ensure_default_pk_holidays(
        db: AsyncSession,
        tenant_id: UUID | str,
        year: int | None = None,
    ) -> None:
        """
        Seeds editable fixed-date Pakistan public holidays for `year` when the tenant has
        zero `default_pk` holidays in that year. Race-safe via `ON CONFLICT DO NOTHING`,
        preserving any custom holidays on the same dates.
        """
        t_id = UUID(str(tenant_id))
        target_year = year if year is not None else _current_pkt_year()
        start_d = date(target_year, 1, 1)
        end_d = date(target_year, 12, 31)

        existing = int(
            (
                await db.execute(
                    select(func.count(Holiday.id)).where(
                        Holiday.tenant_id == t_id,
                        Holiday.source == "default_pk",
                        Holiday.holiday_date >= start_d,
                        Holiday.holiday_date <= end_d,
                    )
                )
            ).scalar()
            or 0
        )
        if existing == 0:
            rows = [
                {
                    "tenant_id": t_id,
                    "holiday_date": date(target_year, month, day),
                    "name": name,
                    "is_optional": False,
                    "source": "default_pk",
                }
                for month, day, name in DEFAULT_PK_FIXED_HOLIDAYS
            ]
            stmt = (
                pg_insert(Holiday)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["tenant_id", "holiday_date"])
            )
            await db.execute(stmt)
            await db.flush()

    @classmethod
    async def list_holidays(
        cls,
        db: AsyncSession,
        tenant_id: UUID | str,
        *,
        year: int | None = None,
    ) -> list[HolidayResponse]:
        """Lists tenant holidays (optionally filtered by year), seeding PK defaults if empty."""
        t_id = UUID(str(tenant_id))
        target_year = year if year is not None else _current_pkt_year()
        await cls.ensure_default_pk_holidays(db, t_id, year=target_year)

        stmt = select(Holiday).where(Holiday.tenant_id == t_id)
        if year is not None:
            stmt = stmt.where(
                Holiday.holiday_date >= date(year, 1, 1),
                Holiday.holiday_date <= date(year, 12, 31),
            )
        stmt = stmt.order_by(Holiday.holiday_date.asc())
        rows = (await db.execute(stmt)).scalars().all()
        return [HolidayResponse.model_validate(r) for r in rows]

    @staticmethod
    async def create_holiday(
        db: AsyncSession,
        tenant_id: UUID | str,
        payload: HolidayCreateRequest,
        actor_id: UUID | str,
    ) -> HolidayResponse:
        """Creates a tenant holiday, rejecting duplicate dates with HTTP 409."""
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id))
        dup = (
            await db.execute(
                select(Holiday.id).where(
                    Holiday.tenant_id == t_id,
                    Holiday.holiday_date == payload.holiday_date,
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"DUPLICATE_HOLIDAY_DATE: Holiday on '{payload.holiday_date}' already exists.",
            )

        hol = Holiday(
            tenant_id=t_id,
            holiday_date=payload.holiday_date,
            name=payload.name,
            is_optional=payload.is_optional,
            source=payload.source,
        )
        db.add(hol)
        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="leave_config.holiday_created",
            target_type="holiday",
            target_id=hol.id,
            details={"holiday_date": hol.holiday_date.isoformat(), "name": hol.name},
        )
        return HolidayResponse.model_validate(hol)

    @staticmethod
    async def update_holiday(
        db: AsyncSession,
        tenant_id: UUID | str,
        holiday_id: UUID,
        payload: HolidayUpdateRequest,
        actor_id: UUID | str,
    ) -> HolidayResponse:
        """Updates a tenant holiday, checking date uniqueness if `holiday_date` changes."""
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id))
        hol = (
            await db.execute(
                select(Holiday).where(
                    Holiday.tenant_id == t_id,
                    Holiday.id == holiday_id,
                )
            )
        ).scalar_one_or_none()
        if hol is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Holiday '{holiday_id}' not found.",
            )

        fields_set = payload.model_fields_set
        if (
            "holiday_date" in fields_set
            and payload.holiday_date is not None
            and payload.holiday_date != hol.holiday_date
        ):
            dup = (
                await db.execute(
                    select(Holiday.id).where(
                        Holiday.tenant_id == t_id,
                        Holiday.holiday_date == payload.holiday_date,
                        Holiday.id != hol.id,
                    )
                )
            ).scalar_one_or_none()
            if dup is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"DUPLICATE_HOLIDAY_DATE: Holiday on '{payload.holiday_date}' already exists.",
                )
            hol.holiday_date = payload.holiday_date
        if "name" in fields_set and payload.name is not None:
            hol.name = payload.name
        if "is_optional" in fields_set and payload.is_optional is not None:
            hol.is_optional = payload.is_optional

        hol.updated_at = datetime.now(UTC)
        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="leave_config.holiday_updated",
            target_type="holiday",
            target_id=hol.id,
            details={"holiday_date": hol.holiday_date.isoformat(), "name": hol.name},
        )
        return HolidayResponse.model_validate(hol)

    @staticmethod
    async def delete_holiday(
        db: AsyncSession,
        tenant_id: UUID | str,
        holiday_id: UUID,
        actor_id: UUID | str,
    ) -> HolidayDeleteResponse:
        """Deletes a tenant holiday."""
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id))
        hol = (
            await db.execute(
                select(Holiday).where(
                    Holiday.tenant_id == t_id,
                    Holiday.id == holiday_id,
                )
            )
        ).scalar_one_or_none()
        if hol is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Holiday '{holiday_id}' not found.",
            )

        hol_date = hol.holiday_date
        hol_name = hol.name
        await db.delete(hol)
        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="leave_config.holiday_deleted",
            target_type="holiday",
            target_id=holiday_id,
            details={"holiday_date": hol_date.isoformat(), "name": hol_name},
        )
        return HolidayDeleteResponse(id=holiday_id, holiday_date=hol_date)


# Ensure Attendance model is imported in service module scope if needed for type checks
_ATTENDANCE_MODEL = Attendance
