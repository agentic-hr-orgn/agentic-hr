"""
Stage 6 Prompt 2 Verification Suite:
1. Pure Leave Policy Check Engine (`leave_policy_engine.check_leave`) unit tests covering
   all 9 rules and boundary semantics (zero DB/network/clock side effects).
2. PostgreSQL + Forced RLS idempotency & ledger reconciliation tests for:
   - `run_monthly_accrual` (monthly-only, `idempotency_key`, zero double-credit on re-run)
   - `run_year_end_carry_forward` (cap enforcement, shared `init-balance` key, ledger reconciliation)
3. Sandboxed Temporal workflow tests (`LeaveAccrualWorkflow`, `LeaveCarryForwardWorkflow`)
   via `WorkflowEnvironment.start_time_skipping()` and idempotent `ensure_leave_accrual_schedules`.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from temporalio.client import Schedule, ScheduleAlreadyRunningError, ScheduleUpdateInput
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.db.session import AsyncSessionLocal
from app.models.tenant import set_tenant_context
from app.services.leave_accrual_service import (
    run_monthly_accrual,
    run_year_end_carry_forward,
)
from app.services.leave_config_service import initialize_employee_leave_balances
from app.services.leave_policy_engine import (
    BalanceSnapshot,
    EmployeeCheckState,
    ExistingLeaveSpan,
    LeaveCheckInput,
    TeamCoverageState,
    add_calendar_months,
    check_leave,
)
from app.worker import (
    ANNUAL_LEAVE_CARRY_FORWARD_CRON,
    ANNUAL_LEAVE_CARRY_FORWARD_SCHEDULE_ID,
    LEAVE_SCHEDULE_TZ,
    MONTHLY_LEAVE_ACCRUAL_CRON,
    MONTHLY_LEAVE_ACCRUAL_SCHEDULE_ID,
    ensure_leave_accrual_schedules,
)
from app.workflows import (
    LeaveAccrualWorkflow,
    LeaveAccrualWorkflowInput,
    LeaveCarryForwardWorkflow,
    LeaveCarryForwardWorkflowInput,
    list_active_tenant_ids_activity,
    run_tenant_carry_forward_activity,
    run_tenant_monthly_accrual_activity,
)
from tests.isolation_harness import MIGRATION_DB_URL, MultiTenantTestContext


@pytest_asyncio.fixture(autouse=True)
async def _preserve_globex_manager_flags(
    isolation_context: MultiTenantTestContext,
) -> AsyncIterator[None]:
    """Snapshots and restores globex.manager's membership flags across every test."""
    membership_id = isolation_context.company_b.manager.membership_id
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    orig = await conn.fetchrow(
        "SELECT is_hr_head, is_payroll_admin FROM public.memberships WHERE id = $1::uuid;",
        membership_id,
    )
    await conn.close()
    try:
        yield
    finally:
        if orig is not None:
            conn = await asyncpg.connect(MIGRATION_DB_URL)
            try:
                await conn.execute(
                    "UPDATE public.memberships SET is_hr_head = $1, is_payroll_admin = $2 "
                    "WHERE id = $3::uuid;",
                    orig["is_hr_head"],
                    orig["is_payroll_admin"],
                    membership_id,
                )
            finally:
                await conn.close()


def _base_employee(hire_date: date = date(2024, 1, 10)) -> EmployeeCheckState:
    return EmployeeCheckState(employee_id="emp-001", hire_date=hire_date, status="active")


def _annual_balance(
    *,
    allocated: str = "20.00",
    carried: str = "0.00",
    reserved: str = "0.00",
    used: str = "0.00",
    year: int = 2026,
    code: str = "annual",
) -> dict[str, BalanceSnapshot]:
    return {
        code: BalanceSnapshot(
            leave_type_code=code,
            year=year,
            allocated_days=Decimal(allocated),
            carried_forward_days=Decimal(carried),
            reserved_days=Decimal(reserved),
            used_days=Decimal(used),
        )
    }


# ==============================================================================
# 1. Pure Policy Check Engine Unit Tests (All 9 Rules)
# ==============================================================================
def test_01_normal_annual_leave_ok() -> None:
    req = LeaveCheckInput(
        leave_type_code="annual",
        start_date=date(2026, 5, 11),  # Mon
        end_date=date(2026, 5, 13),  # Wed
        as_of_date=date(2026, 5, 1),
        min_notice_days=3,
        max_consecutive_days=10,
    )
    res = check_leave(
        req,
        _base_employee(),
        _annual_balance(allocated="20.00", used="5.00"),
        holidays=[],
        existing_leaves=[],
        team_state=TeamCoverageState(team_size=10, absent_by_date={}),
    )
    assert res.status == "OK"
    assert res.working_days == Decimal("3.00")
    assert res.reasons == []


def test_02_insufficient_balance_blocked() -> None:
    req = LeaveCheckInput(
        leave_type_code="annual",
        start_date=date(2026, 5, 11),
        end_date=date(2026, 5, 15),  # 5 working days
        as_of_date=date(2026, 5, 1),
    )
    res = check_leave(
        req,
        _base_employee(),
        _annual_balance(allocated="10.00", reserved="4.00", used="4.00"),  # available = 2.00
        holidays=[],
        existing_leaves=[],
    )
    assert res.status == "BLOCKED"
    assert res.working_days == Decimal("5.00")
    assert any(r.code == "INSUFFICIENT_BALANCE" and r.status == "BLOCKED" for r in res.reasons)


def test_03_overlap_detection_full_day_and_half_day() -> None:
    # Full-day overlap with pending request -> BLOCKED
    req = LeaveCheckInput(
        leave_type_code="annual",
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 10),
        as_of_date=date(2026, 6, 1),
    )
    res_overlap = check_leave(
        req,
        _base_employee(),
        _annual_balance(),
        holidays=[],
        existing_leaves=[
            ExistingLeaveSpan(
                start_date=date(2026, 6, 10),
                end_date=date(2026, 6, 12),
                status="pending",
            )
        ],
    )
    assert res_overlap.status == "BLOCKED"
    assert any(r.code == "OVERLAPPING_LEAVE" for r in res_overlap.reasons)

    # Rejected / cancelled spans must NOT block
    res_ignored = check_leave(
        req,
        _base_employee(),
        _annual_balance(),
        holidays=[],
        existing_leaves=[
            ExistingLeaveSpan(
                start_date=date(2026, 6, 8),
                end_date=date(2026, 6, 10),
                status="rejected",
            ),
            ExistingLeaveSpan(
                start_date=date(2026, 6, 8),
                end_date=date(2026, 6, 10),
                status="cancelled",
            ),
        ],
    )
    assert res_ignored.status == "OK"

    # Half-day morning + existing afternoon on same day -> OK (no conflict)
    req_half_am = LeaveCheckInput(
        leave_type_code="casual",
        start_date=date(2026, 6, 15),
        end_date=date(2026, 6, 15),
        as_of_date=date(2026, 6, 1),
        is_half_day=True,
        half_day_period="morning",
    )
    res_half_ok = check_leave(
        req_half_am,
        _base_employee(),
        _annual_balance(code="casual"),
        holidays=[],
        existing_leaves=[
            ExistingLeaveSpan(
                start_date=date(2026, 6, 15),
                end_date=date(2026, 6, 15),
                status="approved",
                is_half_day=True,
                half_day_period="afternoon",
            )
        ],
    )
    assert res_half_ok.status == "OK"
    assert res_half_ok.working_days == Decimal("0.50")

    # Half-day morning + existing morning -> BLOCKED
    res_half_clash = check_leave(
        req_half_am,
        _base_employee(),
        _annual_balance(code="casual"),
        holidays=[],
        existing_leaves=[
            ExistingLeaveSpan(
                start_date=date(2026, 6, 15),
                end_date=date(2026, 6, 15),
                status="approved",
                is_half_day=True,
                half_day_period="morning",
            )
        ],
    )
    assert res_half_clash.status == "BLOCKED"
    assert any(r.code == "OVERLAPPING_LEAVE" for r in res_half_clash.reasons)


def test_04_half_day_rules_and_holiday_weekend_exclusions() -> None:
    # Half day disallowed by leave type -> BLOCKED
    req_disallowed = LeaveCheckInput(
        leave_type_code="annual",
        start_date=date(2026, 5, 11),
        end_date=date(2026, 5, 11),
        as_of_date=date(2026, 5, 1),
        is_half_day=True,
        half_day_period="morning",
        allow_half_day=False,
    )
    res_dis = check_leave(req_disallowed, _base_employee(), _annual_balance(), [], [])
    assert res_dis.status == "BLOCKED"
    assert any(r.code == "HALF_DAY_NOT_ALLOWED" for r in res_dis.reasons)

    # Range spanning weekend + Pakistan Independence Day (Fri 2026-08-14)
    # Thu Aug 13 (work), Fri Aug 14 (holiday), Sat Aug 15 (weekend), Sun Aug 16 (weekend), Mon Aug 17 (work) => 2 working days
    req_span = LeaveCheckInput(
        leave_type_code="annual",
        start_date=date(2026, 8, 13),
        end_date=date(2026, 8, 17),
        as_of_date=date(2026, 8, 1),
    )
    res_span = check_leave(
        req_span,
        _base_employee(),
        _annual_balance(),
        holidays=[date(2026, 8, 14)],
        existing_leaves=[],
    )
    assert res_span.status == "OK"
    assert res_span.working_days == Decimal("2.00")

    # Weekend-only range -> BLOCKED (NO_WORKING_DAYS)
    req_wknd = LeaveCheckInput(
        leave_type_code="annual",
        start_date=date(2026, 8, 15),  # Sat
        end_date=date(2026, 8, 16),  # Sun
        as_of_date=date(2026, 8, 1),
    )
    res_wknd = check_leave(req_wknd, _base_employee(), _annual_balance(), [], [])
    assert res_wknd.status == "BLOCKED"
    assert res_wknd.working_days == Decimal("0.00")
    assert any(r.code == "NO_WORKING_DAYS" for r in res_wknd.reasons)


def test_05_probation_max_consecutive_and_notice_violations() -> None:
    # Calendar month clamping sanity check
    assert add_calendar_months(date(2026, 1, 31), 1) == date(2026, 2, 28)

    # Hired 2026-03-01, probation 3 months -> ends 2026-06-01; start_date 2026-05-11 -> DURING_PROBATION
    new_emp = _base_employee(hire_date=date(2026, 3, 1))
    req_prob = LeaveCheckInput(
        leave_type_code="annual",
        start_date=date(2026, 5, 11),
        end_date=date(2026, 5, 15),  # 5 days > max 3
        as_of_date=date(2026, 5, 10),  # 1 day notice < min 3
        probation_months=3,
        allow_during_probation=False,
        max_consecutive_days=3,
        min_notice_days=3,
    )
    res = check_leave(req_prob, new_emp, _annual_balance(), [], [])
    assert res.status == "VIOLATION"
    codes = {r.code for r in res.reasons}
    assert {"DURING_PROBATION", "EXCEEDS_MAX_CONSECUTIVE_DAYS", "SHORT_NOTICE"} == codes


def test_06_sick_leave_document_threshold_and_team_coverage() -> None:
    # requires_document_after_days = 2 => strict `>` means 2 days OK without doc, 3 days BLOCKED without doc
    req_2d = LeaveCheckInput(
        leave_type_code="sick",
        start_date=date(2026, 5, 11),
        end_date=date(2026, 5, 12),  # 2 days
        as_of_date=date(2026, 5, 11),
        requires_document_after_days=2,
        document_id=None,
    )
    res_2d = check_leave(req_2d, _base_employee(), _annual_balance(code="sick"), [], [])
    assert res_2d.status == "OK"
    assert res_2d.working_days == Decimal("2.00")

    req_3d_missing = LeaveCheckInput(
        leave_type_code="sick",
        start_date=date(2026, 5, 11),
        end_date=date(2026, 5, 13),  # 3 days
        as_of_date=date(2026, 5, 11),
        requires_document_after_days=2,
        document_id=None,
    )
    res_3d_blocked = check_leave(
        req_3d_missing, _base_employee(), _annual_balance(code="sick"), [], []
    )
    assert res_3d_blocked.status == "BLOCKED"
    assert any(r.code == "DOCUMENT_REQUIRED" for r in res_3d_blocked.reasons)

    req_3d_attached = req_3d_missing.model_copy(update={"document_id": str(uuid.uuid4())})
    res_3d_ok = check_leave(req_3d_attached, _base_employee(), _annual_balance(code="sick"), [], [])
    assert res_3d_ok.status == "OK"

    # Team coverage: team_size=4, 1 already absent on 2026-05-12 -> +1 = 2/4 = 50% > 30% -> VIOLATION
    res_team = check_leave(
        req_2d,
        _base_employee(),
        _annual_balance(code="sick"),
        [],
        [],
        team_state=TeamCoverageState(
            team_size=4,
            absent_by_date={date(2026, 5, 12): 1},
            max_team_absent_pct=Decimal("30.00"),
            min_team_present=1,
        ),
    )
    assert res_team.status == "VIOLATION"
    assert any(r.code == "TEAM_COVERAGE_EXCEEDED" for r in res_team.reasons)

    # team_size == 0 must NOT divide by zero
    res_zero_team = check_leave(
        req_2d,
        _base_employee(),
        _annual_balance(code="sick"),
        [],
        [],
        team_state=TeamCoverageState(team_size=0, absent_by_date={}),
    )
    assert res_zero_team.status == "OK"


# ==============================================================================
# 2. DB Accrual, Carry-Forward Cap & Ledger Reconciliation Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_07_monthly_accrual_and_year_end_carry_forward_idempotency(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - `run_monthly_accrual` credits `annual_allowance_days / 12` ONLY for `monthly` types,
      is strictly idempotent on re-run, and reconciles with `leave_ledger`.
    - `run_year_end_carry_forward` shares `init-balance:{emp}:{lt}:{to_year}` with
      `initialize_employee_leave_balances()`, caps carried-forward days at `carry_forward_cap_days`,
      and reconciles `SUM(delta_allocated) == allocated_days + carried_forward_days`.
    """
    globex = isolation_context.company_b
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)

    emp_id = str(uuid.uuid4())
    lt_monthly_id = str(uuid.uuid4())
    lt_upfront_id = str(uuid.uuid4())

    try:
        for tbl in (
            "leave_requests",
            "leave_ledger",
            "leave_balances",
            "leave_types",
        ):
            await admin_conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )

        await admin_conn.execute(
            """
            INSERT INTO public.employees (id, tenant_id, full_name, email, job_title, join_date, status)
            VALUES ($1::uuid, $2::uuid, 'Accrual Test Emp', $3, 'Engineer', $4, 'active');
            """,
            emp_id,
            globex.tenant_id,
            f"accrual-{uuid.uuid4().hex[:6]}@globex.internal",
            date(2025, 1, 1),
        )
        await admin_conn.execute(
            """
            INSERT INTO public.leave_types (
                id, tenant_id, code, name, annual_allowance_days, accrual_mode,
                carry_forward_cap_days, is_active
            )
            VALUES
              ($1::uuid, $2::uuid, 'earned_monthly', 'Earned Monthly', 18.00, 'monthly', 4.00, true),
              ($3::uuid, $2::uuid, 'annual_upfront', 'Annual Upfront', 20.00, 'upfront', 5.00, true);
            """,
            lt_monthly_id,
            globex.tenant_id,
            lt_upfront_id,
        )

        # Initialize 2026 balances via Prompt 1 helper (upfront=20.00, monthly=0.00)
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.clerk_user_id)
            created = await initialize_employee_leave_balances(
                db,
                tenant_id=globex.tenant_id,
                employee_id=emp_id,
                year=2026,
                actor_id=globex.owner.user_id,
            )
            assert created == 2
            await db.commit()

        # Run monthly accrual for 2026-01 twice -> 18/12 = 1.50 credited ONCE
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.clerk_user_id)
            s1 = await run_monthly_accrual(db, globex.tenant_id, "2026-01", globex.owner.user_id)
            await db.commit()
            assert s1["credited_count"] == 1
            assert s1["skipped_count"] == 0
            assert Decimal(s1["total_days_credited"]) == Decimal("1.50")

        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.clerk_user_id)
            s2 = await run_monthly_accrual(db, globex.tenant_id, "2026-01", globex.owner.user_id)
            await db.commit()
            assert s2["credited_count"] == 0
            assert s2["skipped_count"] == 1

        # Simulate usage on 2026 annual_upfront: 20 - 8 = 12 remaining -> capped at 5.00 for 2027
        await admin_conn.execute(
            """
            UPDATE public.leave_balances
            SET used_days = 8.00
            WHERE tenant_id = $1::uuid AND employee_id = $2::uuid
              AND leave_type_id = $3::uuid AND year = 2026;
            """,
            globex.tenant_id,
            emp_id,
            lt_upfront_id,
        )

        # Pre-initialize 2027 via Prompt 1 helper to prove shared `init-balance` key prevents double allocation
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.clerk_user_id)
            pre_init = await initialize_employee_leave_balances(
                db,
                tenant_id=globex.tenant_id,
                employee_id=emp_id,
                year=2027,
                actor_id=globex.owner.user_id,
            )
            assert pre_init == 2
            await db.commit()

        # Run year-end carry-forward 2026 -> 2027 twice
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.clerk_user_id)
            cf1 = await run_year_end_carry_forward(
                db, globex.tenant_id, 2026, 2027, globex.owner.user_id
            )
            await db.commit()
            # initialized_count == 0 because init-balance key already existed!
            assert cf1["initialized_count"] == 0
            # Carried: annual_upfront = min(12.00, 5.00) = 5.00; earned_monthly = min(1.50, 4.00) = 1.50
            assert cf1["carried_count"] == 2
            assert Decimal(cf1["total_days_carried"]) == Decimal("6.50")

        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.clerk_user_id)
            cf2 = await run_year_end_carry_forward(
                db, globex.tenant_id, 2026, 2027, globex.owner.user_id
            )
            await db.commit()
            assert cf2["initialized_count"] == 0
            assert cf2["carried_count"] == 0
            assert cf2["skipped_count"] == 2

        # Verify 2027 balances & ledger reconciliation: SUM(delta_allocated) == allocated + carried_forward
        rows_2027 = await admin_conn.fetch(
            """
            SELECT
                lt.code,
                lb.allocated_days,
                lb.carried_forward_days,
                COALESCE(SUM(ll.delta_allocated), 0) AS ledger_sum
            FROM public.leave_balances lb
            JOIN public.leave_types lt ON lt.id = lb.leave_type_id
            LEFT JOIN public.leave_ledger ll ON ll.balance_id = lb.id
            WHERE lb.tenant_id = $1::uuid AND lb.employee_id = $2::uuid AND lb.year = 2027
            GROUP BY lt.code, lb.allocated_days, lb.carried_forward_days;
            """,
            globex.tenant_id,
            emp_id,
        )
        by_code = {r["code"]: r for r in rows_2027}
        assert Decimal(str(by_code["annual_upfront"]["allocated_days"])) == Decimal("20.00")
        assert Decimal(str(by_code["annual_upfront"]["carried_forward_days"])) == Decimal("5.00")
        assert Decimal(str(by_code["annual_upfront"]["ledger_sum"])) == Decimal("25.00")

        assert Decimal(str(by_code["earned_monthly"]["allocated_days"])) == Decimal("0.00")
        assert Decimal(str(by_code["earned_monthly"]["carried_forward_days"])) == Decimal("1.50")
        assert Decimal(str(by_code["earned_monthly"]["ledger_sum"])) == Decimal("1.50")
    finally:
        for tbl in ("leave_requests", "leave_ledger", "leave_balances", "leave_types"):
            await admin_conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
        await admin_conn.execute("DELETE FROM public.employees WHERE id = $1::uuid;", emp_id)
        await admin_conn.close()


# ==============================================================================
# 3. Sandboxed Temporal Workflow & Idempotent Schedule Registration Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_08_sandboxed_leave_accrual_workflow_and_schedule_helper(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Runs `LeaveAccrualWorkflow` and `LeaveCarryForwardWorkflow` inside Temporal's
    sandboxed time-skipping environment and verifies `ensure_leave_accrual_schedules`.
    """
    globex = isolation_context.company_b
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    emp_id = str(uuid.uuid4())
    lt_id = str(uuid.uuid4())

    try:
        for tbl in ("leave_requests", "leave_ledger", "leave_balances", "leave_types"):
            await admin_conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )

        await admin_conn.execute(
            """
            INSERT INTO public.employees (id, tenant_id, full_name, email, job_title, join_date, status)
            VALUES ($1::uuid, $2::uuid, 'Workflow Emp', $3, 'Analyst', $4, 'active');
            """,
            emp_id,
            globex.tenant_id,
            f"wf-emp-{uuid.uuid4().hex[:6]}@globex.internal",
            date(2025, 6, 1),
        )
        await admin_conn.execute(
            """
            INSERT INTO public.leave_types (
                id, tenant_id, code, name, annual_allowance_days, accrual_mode,
                carry_forward_cap_days, is_active
            )
            VALUES ($1::uuid, $2::uuid, 'wf_monthly', 'WF Monthly', 24.00, 'monthly', 3.00, true);
            """,
            lt_id,
            globex.tenant_id,
        )

        env = await WorkflowEnvironment.start_time_skipping()
        task_queue = "test-stage6-leave-accrual-q"
        async with (
            env,
            Worker(
                env.client,
                task_queue=task_queue,
                workflows=[LeaveAccrualWorkflow, LeaveCarryForwardWorkflow],
                activities=[
                    list_active_tenant_ids_activity,
                    run_tenant_monthly_accrual_activity,
                    run_tenant_carry_forward_activity,
                ],
            ),
        ):
            res_accrual = await env.client.execute_workflow(
                LeaveAccrualWorkflow.run,
                LeaveAccrualWorkflowInput(year_month="2026-03", tenant_ids=[globex.tenant_id]),
                id=f"test-accrual-{uuid.uuid4().hex[:8]}",
                task_queue=task_queue,
            )
            assert res_accrual["tenants_processed"] == 1
            assert res_accrual["credited_count"] == 1
            assert res_accrual["failed_tenants"] == []

            # Re-run same month -> idempotent skip
            res_accrual_2 = await env.client.execute_workflow(
                LeaveAccrualWorkflow.run,
                LeaveAccrualWorkflowInput(year_month="2026-03", tenant_ids=[globex.tenant_id]),
                id=f"test-accrual-dup-{uuid.uuid4().hex[:8]}",
                task_queue=task_queue,
            )
            assert res_accrual_2["credited_count"] == 0
            assert res_accrual_2["skipped_count"] == 1

            # Carry-forward 2026 -> 2027 (2.00 remaining <= 3.00 cap => 2.00 carried)
            res_cf = await env.client.execute_workflow(
                LeaveCarryForwardWorkflow.run,
                LeaveCarryForwardWorkflowInput(
                    from_year=2026,
                    to_year=2027,
                    tenant_ids=[globex.tenant_id],
                ),
                id=f"test-cf-{uuid.uuid4().hex[:8]}",
                task_queue=task_queue,
            )
            assert res_cf["tenants_processed"] == 1
            assert res_cf["carried_count"] == 1
            assert res_cf["failed_tenants"] == []

        # Verify schedule helper idempotency using lightweight mock Temporal client
        class _FakeScheduleHandle:
            def __init__(self, store: dict[str, Schedule], schedule_id: str) -> None:
                self._store = store
                self._schedule_id = schedule_id
                self.update_calls = 0

            async def update(self, updater: Any) -> None:
                self.update_calls += 1
                result = await updater(
                    ScheduleUpdateInput(description=None)  # type: ignore[arg-type]
                )
                self._store[self._schedule_id] = result.schedule

        class _FakeTemporalClient:
            def __init__(self) -> None:
                self.schedules: dict[str, Schedule] = {}
                self.handles: dict[str, _FakeScheduleHandle] = {}

            async def create_schedule(self, schedule_id: str, schedule: Schedule) -> None:
                if schedule_id in self.schedules:
                    raise ScheduleAlreadyRunningError()
                self.schedules[schedule_id] = schedule

            def get_schedule_handle(self, schedule_id: str) -> _FakeScheduleHandle:
                if schedule_id not in self.handles:
                    self.handles[schedule_id] = _FakeScheduleHandle(self.schedules, schedule_id)
                return self.handles[schedule_id]

        fake_client = _FakeTemporalClient()
        out1 = await ensure_leave_accrual_schedules(fake_client)  # type: ignore[arg-type]
        out2 = await ensure_leave_accrual_schedules(fake_client)  # type: ignore[arg-type]

        assert out1 == {
            MONTHLY_LEAVE_ACCRUAL_SCHEDULE_ID: "created",
            ANNUAL_LEAVE_CARRY_FORWARD_SCHEDULE_ID: "created",
        }
        assert out2 == {
            MONTHLY_LEAVE_ACCRUAL_SCHEDULE_ID: "updated",
            ANNUAL_LEAVE_CARRY_FORWARD_SCHEDULE_ID: "updated",
        }
        assert list(
            fake_client.schedules[MONTHLY_LEAVE_ACCRUAL_SCHEDULE_ID].spec.cron_expressions
        ) == [MONTHLY_LEAVE_ACCRUAL_CRON]
        assert list(
            fake_client.schedules[ANNUAL_LEAVE_CARRY_FORWARD_SCHEDULE_ID].spec.cron_expressions
        ) == [ANNUAL_LEAVE_CARRY_FORWARD_CRON]
        assert (
            fake_client.schedules[MONTHLY_LEAVE_ACCRUAL_SCHEDULE_ID].spec.time_zone_name
            == LEAVE_SCHEDULE_TZ
        )
    finally:
        for tbl in ("leave_requests", "leave_ledger", "leave_balances", "leave_types"):
            await admin_conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
        await admin_conn.execute("DELETE FROM public.employees WHERE id = $1::uuid;", emp_id)
        await admin_conn.close()
