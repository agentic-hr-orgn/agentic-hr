"""
Stage 6 Prompt 1 Verification Suite:
- Migration 0017 tables, Forced RLS, `leave_ledger` runtime immutability & 6-point tenant isolation
- Secure views (`pub_attendance`, `pub_leave`) with `security_invoker=true`, `security_barrier=true`
- Stage 2 approval schemas & live API creation (`leave.exception`, `attendance.post_lock_edit`)
- Configuration REST APIs (`/api/v1/leave/config/work-week`, `/types`, `/holidays`) + Step 3 sync
- Automatic `leave_balances` & `leave_ledger` (`accrual`) creation on employee create & Excel import,
  including `upfront` vs `monthly` accrual_mode handling and idempotency.
"""

import io
import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import asyncpg
import httpx
import openpyxl
import pytest
import pytest_asyncio

from app.db.session import AsyncSessionLocal
from app.models.tenant import set_tenant_context
from app.schemas.approval import validate_approval_payload
from app.services.leave_config_service import initialize_employee_leave_balances
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    MultiTenantTestContext,
    assert_table_tenant_isolation,
    get_app_connection,
)

STAGE6_TABLES: tuple[str, ...] = (
    "leave_types",
    "holidays",
    "work_week",
    "leave_balances",
    "leave_ledger",
    "leave_requests",
    "attendance",
    "attendance_month_locks",
)

STAGE6_VIEWS: tuple[str, ...] = (
    "pub_attendance",
    "pub_leave",
)


@pytest_asyncio.fixture(autouse=True)
async def _preserve_globex_manager_flags(
    isolation_context: MultiTenantTestContext,
) -> AsyncIterator[None]:
    """Snapshots and restores globex.manager's `is_hr_head` & `is_payroll_admin` flags."""
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


async def _cleanup_globex_stage6(tenant_id: str) -> None:
    """Removes Stage 6 config, balances, requests, attendance, and test employees for Globex."""
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "attendance",
            "attendance_month_locks",
            "leave_requests",
            "leave_ledger",
            "leave_balances",
            "leave_types",
            "holidays",
            "work_week",
        ):
            await conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                tenant_id,
            )
    finally:
        await conn.close()


def _build_import_xlsx(rows: list[list[str]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Employees"
    ws.append(
        [
            "full_name",
            "email",
            "phone",
            "department",
            "manager_email",
            "job_title",
            "employment_type",
            "join_date",
            "cnic",
            "bank_account",
        ]
    )
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    try:
        wb.save(buf)
    finally:
        wb.close()
    return buf.getvalue()


# ==============================================================================
# 1. Migration 0017 Schema, Forced RLS, Ledger Immutability & 6-Point Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_01_stage6_tables_forced_rls_and_ledger_immutability(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies all 8 Stage 6 tables have Forced RLS, `agentic_hr_migrator` ownership,
    revoked `TRUNCATE`, 6-point cross-tenant isolation, and `leave_ledger` runtime immutability.
    """
    acme = isolation_context.company_a
    globex = isolation_context.company_b

    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    created_ids: dict[str, tuple[str, str]] = {}
    emp_a_id: str | None = None
    emp_b_id: str | None = None

    try:
        # Verify Forced RLS & ownership metadata
        meta_rows = await admin_conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) AS owner
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY($1::text[]);
            """,
            list(STAGE6_TABLES),
        )
        meta_by_name = {r["relname"]: r for r in meta_rows}
        assert set(meta_by_name.keys()) == set(STAGE6_TABLES)
        for tbl in STAGE6_TABLES:
            row = meta_by_name[tbl]
            assert row["relrowsecurity"] is True, f"{tbl} missing ENABLE ROW LEVEL SECURITY"
            assert row["relforcerowsecurity"] is True, f"{tbl} missing FORCE ROW LEVEL SECURITY"
            assert row["owner"] == "agentic_hr_migrator", f"{tbl} wrong owner: {row['owner']}"

        # Seed isolated test rows in Acme & Globex for 6-point isolation checks
        emp_a_id = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.employees (tenant_id, full_name, email, job_title, join_date, status)
                VALUES ($1::uuid, 'Stage6 Acme Emp', $2, 'Engineer', $3, 'active')
                RETURNING id;
                """,
                acme.tenant_id,
                f"s6-acme-{uuid.uuid4().hex[:6]}@acme.internal",
                date(2025, 1, 10),
            )
        )
        emp_b_id = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.employees (tenant_id, full_name, email, job_title, join_date, status)
                VALUES ($1::uuid, 'Stage6 Globex Emp', $2, 'Engineer', $3, 'active')
                RETURNING id;
                """,
                globex.tenant_id,
                f"s6-globex-{uuid.uuid4().hex[:6]}@globex.internal",
                date(2025, 1, 10),
            )
        )

        lt_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.leave_types (tenant_id, code, name, annual_allowance_days)
                VALUES ($1::uuid, $2, 'Test Annual', 20.00)
                RETURNING id;
                """,
                acme.tenant_id,
                f"s6t-{uuid.uuid4().hex[:6]}",
            )
        )
        lt_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.leave_types (tenant_id, code, name, annual_allowance_days)
                VALUES ($1::uuid, $2, 'Test Annual', 20.00)
                RETURNING id;
                """,
                globex.tenant_id,
                f"s6t-{uuid.uuid4().hex[:6]}",
            )
        )
        created_ids["leave_types"] = (lt_a, lt_b)

        hol_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.holidays (tenant_id, holiday_date, name, source)
                VALUES ($1::uuid, $2, 'Acme Holiday', 'custom')
                RETURNING id;
                """,
                acme.tenant_id,
                date(2030, 6, 15),
            )
        )
        hol_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.holidays (tenant_id, holiday_date, name, source)
                VALUES ($1::uuid, $2, 'Globex Holiday', 'custom')
                RETURNING id;
                """,
                globex.tenant_id,
                date(2030, 6, 16),
            )
        )
        created_ids["holidays"] = (hol_a, hol_b)

        ww_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.work_week (tenant_id)
                VALUES ($1::uuid)
                ON CONFLICT (tenant_id) DO UPDATE SET updated_at = now()
                RETURNING id;
                """,
                acme.tenant_id,
            )
        )
        ww_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.work_week (tenant_id)
                VALUES ($1::uuid)
                ON CONFLICT (tenant_id) DO UPDATE SET updated_at = now()
                RETURNING id;
                """,
                globex.tenant_id,
            )
        )
        created_ids["work_week"] = (ww_a, ww_b)

        bal_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.leave_balances (tenant_id, employee_id, leave_type_id, year, allocated_days)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 2026, 20.00)
                RETURNING id;
                """,
                acme.tenant_id,
                emp_a_id,
                lt_a,
            )
        )
        bal_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.leave_balances (tenant_id, employee_id, leave_type_id, year, allocated_days)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 2026, 20.00)
                RETURNING id;
                """,
                globex.tenant_id,
                emp_b_id,
                lt_b,
            )
        )
        created_ids["leave_balances"] = (bal_a, bal_b)

        led_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.leave_ledger (tenant_id, employee_id, leave_type_id, balance_id, year, entry_type, delta_allocated)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, 2026, 'accrual', 20.00)
                RETURNING id;
                """,
                acme.tenant_id,
                emp_a_id,
                lt_a,
                bal_a,
            )
        )
        led_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.leave_ledger (tenant_id, employee_id, leave_type_id, balance_id, year, entry_type, delta_allocated)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, 2026, 'accrual', 20.00)
                RETURNING id;
                """,
                globex.tenant_id,
                emp_b_id,
                lt_b,
                bal_b,
            )
        )
        created_ids["leave_ledger"] = (led_a, led_b)

        req_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.leave_requests (tenant_id, employee_id, leave_type_id, start_date, end_date, working_days, reason)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $4, 1.00, 'Family event')
                RETURNING id;
                """,
                acme.tenant_id,
                emp_a_id,
                lt_a,
                date(2026, 12, 1),
            )
        )
        req_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.leave_requests (tenant_id, employee_id, leave_type_id, start_date, end_date, working_days, reason)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $4, 1.00, 'Personal day')
                RETURNING id;
                """,
                globex.tenant_id,
                emp_b_id,
                lt_b,
                date(2026, 12, 2),
            )
        )
        created_ids["leave_requests"] = (req_a, req_b)

        att_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.attendance (tenant_id, employee_id, attendance_date, status, source)
                VALUES ($1::uuid, $2::uuid, $3, 'PRESENT', 'check_in')
                RETURNING id;
                """,
                acme.tenant_id,
                emp_a_id,
                date(2026, 11, 20),
            )
        )
        att_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.attendance (tenant_id, employee_id, attendance_date, status, source)
                VALUES ($1::uuid, $2::uuid, $3, 'WFH', 'manager_grid')
                RETURNING id;
                """,
                globex.tenant_id,
                emp_b_id,
                date(2026, 11, 20),
            )
        )
        created_ids["attendance"] = (att_a, att_b)

        ml_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.attendance_month_locks (tenant_id, year_month, employee_count, record_count)
                VALUES ($1::uuid, '2030-01', 1, 1)
                RETURNING id;
                """,
                acme.tenant_id,
            )
        )
        ml_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.attendance_month_locks (tenant_id, year_month, employee_count, record_count)
                VALUES ($1::uuid, '2030-01', 1, 1)
                RETURNING id;
                """,
                globex.tenant_id,
            )
        )
        created_ids["attendance_month_locks"] = (ml_a, ml_b)

        for tbl in STAGE6_TABLES:
            id_a, id_b = created_ids[tbl]
            await assert_table_tenant_isolation(
                table_name=tbl,
                company_a_tenant_id=acme.tenant_id,
                company_b_tenant_id=globex.tenant_id,
                company_a_row_id=id_a,
                company_b_row_id=id_b,
            )

        # Verify leave_ledger runtime immutability under agentic_hr_app
        app_conn = await get_app_connection()
        try:
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    acme.tenant_id,
                )
                with pytest.raises(
                    (
                        asyncpg.exceptions.InsufficientPrivilegeError,
                        asyncpg.exceptions.RaiseError,
                    )
                ):
                    await app_conn.execute(
                        "UPDATE public.leave_ledger SET note = 'tampered' WHERE id = $1::uuid;",
                        led_a,
                    )

            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    acme.tenant_id,
                )
                with pytest.raises(
                    (
                        asyncpg.exceptions.InsufficientPrivilegeError,
                        asyncpg.exceptions.RaiseError,
                    )
                ):
                    await app_conn.execute(
                        "DELETE FROM public.leave_ledger WHERE id = $1::uuid;",
                        led_a,
                    )

            with pytest.raises(
                (
                    asyncpg.exceptions.InsufficientPrivilegeError,
                    asyncpg.exceptions.RaiseError,
                )
            ):
                await app_conn.execute("TRUNCATE TABLE public.leave_ledger;")
        finally:
            await app_conn.close()
    finally:
        for tbl in (
            "attendance_month_locks",
            "attendance",
            "leave_requests",
            "leave_ledger",
            "leave_balances",
            "leave_types",
            "holidays",
        ):
            if tbl in created_ids:
                await admin_conn.execute(
                    f"DELETE FROM public.{tbl} WHERE id = ANY($1::uuid[]);",
                    list(created_ids[tbl]),
                )
        for emp_id in (emp_a_id, emp_b_id):
            if emp_id:
                await admin_conn.execute(
                    "DELETE FROM public.employees WHERE id = $1::uuid;",
                    emp_id,
                )
        await admin_conn.close()


# ==============================================================================
# 2. Secure Views (`pub_attendance`, `pub_leave`) & Tenant Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_02_secure_views_pub_attendance_and_pub_leave(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies `pub_attendance` & `pub_leave` have `security_invoker=true` & `security_barrier=true`,
    are owned by `agentic_hr_migrator`, and enforce strict RLS under `agentic_hr_app`.
    """
    acme = isolation_context.company_a
    globex = isolation_context.company_b
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)

    emp_a_id = str(uuid.uuid4())
    emp_b_id = str(uuid.uuid4())
    lt_a_id = str(uuid.uuid4())
    lt_b_id = str(uuid.uuid4())
    req_a_id = str(uuid.uuid4())
    req_b_id = str(uuid.uuid4())

    try:
        view_rows = await admin_conn.fetch(
            """
            SELECT c.relname, c.reloptions, pg_get_userbyid(c.relowner) AS owner
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY($1::text[]);
            """,
            list(STAGE6_VIEWS),
        )
        views_by_name = {r["relname"]: r for r in view_rows}
        assert set(views_by_name.keys()) == set(STAGE6_VIEWS)
        for vname in STAGE6_VIEWS:
            opts = set(views_by_name[vname]["reloptions"] or [])
            assert "security_invoker=true" in opts
            assert "security_barrier=true" in opts
            assert views_by_name[vname]["owner"] == "agentic_hr_migrator"

        await admin_conn.execute(
            """
            INSERT INTO public.employees (id, tenant_id, full_name, email, job_title, join_date, status)
            VALUES
              ($1::uuid, $2::uuid, 'View Acme Emp', $3, 'Dev', $4, 'active'),
              ($5::uuid, $6::uuid, 'View Globex Emp', $7, 'Dev', $4, 'active');
            """,
            emp_a_id,
            acme.tenant_id,
            f"view-a-{uuid.uuid4().hex[:6]}@acme.internal",
            date(2025, 2, 1),
            emp_b_id,
            globex.tenant_id,
            f"view-b-{uuid.uuid4().hex[:6]}@globex.internal",
        )
        await admin_conn.execute(
            """
            INSERT INTO public.leave_types (id, tenant_id, code, name, annual_allowance_days)
            VALUES
              ($1::uuid, $2::uuid, $3, 'View Annual', 20.00),
              ($4::uuid, $5::uuid, $6, 'View Annual', 20.00);
            """,
            lt_a_id,
            acme.tenant_id,
            f"va-{uuid.uuid4().hex[:6]}",
            lt_b_id,
            globex.tenant_id,
            f"vb-{uuid.uuid4().hex[:6]}",
        )
        await admin_conn.execute(
            """
            INSERT INTO public.leave_requests (id, tenant_id, employee_id, leave_type_id, start_date, end_date, working_days, reason)
            VALUES
              ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $5, 1.00, 'Acme View Test'),
              ($6::uuid, $7::uuid, $8::uuid, $9::uuid, $5, $5, 1.00, 'Globex View Test');
            """,
            req_a_id,
            acme.tenant_id,
            emp_a_id,
            lt_a_id,
            date(2026, 12, 10),
            req_b_id,
            globex.tenant_id,
            emp_b_id,
            lt_b_id,
        )
        await admin_conn.execute(
            """
            INSERT INTO public.attendance (tenant_id, employee_id, attendance_date, status, source)
            VALUES
              ($1::uuid, $2::uuid, $3, 'PRESENT', 'check_in'),
              ($4::uuid, $5::uuid, $3, 'ABSENT', 'manager_grid');
            """,
            acme.tenant_id,
            emp_a_id,
            date(2026, 11, 21),
            globex.tenant_id,
            emp_b_id,
        )

        app_conn = await get_app_connection()
        try:
            # Uncontexted -> 0 rows
            assert await app_conn.fetchval("SELECT count(*) FROM public.pub_attendance;") == 0
            assert await app_conn.fetchval("SELECT count(*) FROM public.pub_leave;") == 0

            # Bogus tenant -> 0 rows
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', true);"
                )
                assert await app_conn.fetchval("SELECT count(*) FROM public.pub_attendance;") == 0
                assert await app_conn.fetchval("SELECT count(*) FROM public.pub_leave;") == 0

            # Acme context -> sees only Acme rows
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);", acme.tenant_id
                )
                att_rows = await app_conn.fetch(
                    "SELECT tenant_id, employee_id FROM public.pub_attendance WHERE employee_id IN ($1::uuid, $2::uuid);",
                    emp_a_id,
                    emp_b_id,
                )
                assert [str(r["employee_id"]) for r in att_rows] == [emp_a_id]

                lv_rows = await app_conn.fetch(
                    "SELECT request_id FROM public.pub_leave WHERE request_id IN ($1::uuid, $2::uuid);",
                    req_a_id,
                    req_b_id,
                )
                assert [str(r["request_id"]) for r in lv_rows] == [req_a_id]
        finally:
            await app_conn.close()
    finally:
        await admin_conn.execute(
            "DELETE FROM public.attendance WHERE employee_id IN ($1::uuid, $2::uuid);",
            emp_a_id,
            emp_b_id,
        )
        await admin_conn.execute(
            "DELETE FROM public.leave_requests WHERE id IN ($1::uuid, $2::uuid);",
            req_a_id,
            req_b_id,
        )
        await admin_conn.execute(
            "DELETE FROM public.leave_types WHERE id IN ($1::uuid, $2::uuid);",
            lt_a_id,
            lt_b_id,
        )
        await admin_conn.execute(
            "DELETE FROM public.employees WHERE id IN ($1::uuid, $2::uuid);",
            emp_a_id,
            emp_b_id,
        )
        await admin_conn.close()


# ==============================================================================
# 3. Stage 2 Approval Payload Registration (`leave.exception`, `attendance.post_lock_edit`)
# ==============================================================================
@pytest.mark.asyncio
async def test_03_stage6_approval_types_registered_and_validated(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies `leave.exception` & `attendance.post_lock_edit` schemas and live
    `POST /api/v1/approvals` creation + 422 rejection on invalid payloads.
    """
    valid_exc = validate_approval_payload(
        "leave.exception",
        {
            "leave_request_id": str(uuid.uuid4()),
            "employee_id": str(uuid.uuid4()),
            "employee_name": "Sara Khan",
            "leave_type_code": "annual",
            "start_date": "2026-12-01",
            "end_date": "2026-12-03",
            "working_days": 3.0,
            "violations": ["Employee is within probation period"],
            "justification": "Urgent family medical travel",
        },
    )
    assert valid_exc["leave_type_code"] == "annual"

    valid_edit = validate_approval_payload(
        "attendance.post_lock_edit",
        {
            "employee_id": str(uuid.uuid4()),
            "employee_name": "Bilal Ahmed",
            "attendance_date": "2026-11-12",
            "year_month": "2026-11",
            "previous_status": "ABSENT",
            "new_status": "WFH",
            "notes": "Confirmed VPN log",
            "reason": "Forgot to log WFH before month lock",
        },
    )
    assert valid_edit["new_status"] == "WFH"

    with pytest.raises(ValueError):
        validate_approval_payload(
            "attendance.post_lock_edit",
            {
                "employee_id": str(uuid.uuid4()),
                "employee_name": "Bilal Ahmed",
                "attendance_date": "2026-10-12",
                "year_month": "2026-11",
                "new_status": "PRESENT",
                "reason": "Month mismatch should fail",
            },
        )

    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    created_approval_ids: list[str] = []
    try:
        r1 = await async_client.post(
            "/api/v1/approvals",
            headers=emp_headers,
            json={
                "approval_type": "leave.exception",
                "approver_role": "hr_head",
                "payload": valid_exc,
            },
        )
        assert r1.status_code == 201, r1.text
        created_approval_ids.append(r1.json()["id"])

        r2 = await async_client.post(
            "/api/v1/approvals",
            headers=emp_headers,
            json={
                "approval_type": "attendance.post_lock_edit",
                "approver_role": "hr_head",
                "payload": valid_edit,
            },
        )
        assert r2.status_code == 201, r2.text
        created_approval_ids.append(r2.json()["id"])

        r_bad = await async_client.post(
            "/api/v1/approvals",
            headers=emp_headers,
            json={
                "approval_type": "leave.exception",
                "approver_role": "hr_head",
                "payload": {**valid_exc, "start_date": "2026-12-10", "end_date": "2026-12-01"},
            },
        )
        assert r_bad.status_code == 422
    finally:
        if created_approval_ids:
            conn = await asyncpg.connect(MIGRATION_DB_URL)
            try:
                await conn.execute(
                    "DELETE FROM public.approvals WHERE id = ANY($1::uuid[]);",
                    created_approval_ids,
                )
            finally:
                await conn.close()


# ==============================================================================
# 4. Configuration CRUD, RBAC & Onboarding Step 3 Bidirectional Sync
# ==============================================================================
@pytest.mark.asyncio
async def test_04_leave_config_crud_rbac_and_wizard_step3_sync(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies `/api/v1/leave/config/*` CRUD, RBAC, duplicate conflicts, soft-deactivate
    vs hard-delete on `leave_types`, and Onboarding Step 3 `structure_config` sync.
    """
    globex = isolation_context.company_b
    await _cleanup_globex_stage6(globex.tenant_id)
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    # Ensure globex.manager is tested as a plain manager (not HR Head);
    # `_preserve_globex_manager_flags` restores original flags on teardown.
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
            globex.manager.membership_id,
        )
    finally:
        await conn.close()

    try:
        # 1. GET work-week seeds defaults; employee can read, employee/manager blocked on PUT
        ww_get = await async_client.get("/api/v1/leave/config/work-week", headers=emp_headers)
        assert ww_get.status_code == 200, ww_get.text
        assert ww_get.json()["weekend_days"] == ["Saturday", "Sunday"]
        assert ww_get.json()["timezone"] == "Asia/Karachi"

        assert (
            await async_client.put(
                "/api/v1/leave/config/work-week",
                headers=emp_headers,
                json={"probation_months": 6},
            )
        ).status_code == 403
        assert (
            await async_client.put(
                "/api/v1/leave/config/work-week",
                headers=mgr_headers,
                json={"probation_months": 6},
            )
        ).status_code == 403

        # 2. Owner updates work-week including backup_approver_user_id
        ww_put = await async_client.put(
            "/api/v1/leave/config/work-week",
            headers=owner_headers,
            json={
                "weekend_days": ["Friday", "Saturday"],
                "probation_months": 6,
                "max_team_absent_pct": "25.00",
                "min_team_present": 2,
                "backup_approver_user_id": globex.manager.user_id,
                "check_in_enabled": False,
            },
        )
        assert ww_put.status_code == 200, ww_put.text
        assert ww_put.json()["weekend_days"] == ["Friday", "Saturday"]
        assert ww_put.json()["probation_months"] == 6

        # Invalid backup_approver_user_id (outside tenant) -> 422
        ww_bad_approver = await async_client.put(
            "/api/v1/leave/config/work-week",
            headers=owner_headers,
            json={"backup_approver_user_id": str(uuid.uuid4())},
        )
        assert ww_bad_approver.status_code == 422

        # 3. Onboarding Step 3 syncs structure_config -> work_week, leave_types, holidays
        step3_resp = await async_client.put(
            "/api/v1/onboarding/steps/3",
            headers=owner_headers,
            json={
                "structure_config": {
                    "weekends": ["Sunday"],
                    "leave_rules": {"annual_days": 24, "carry_forward_max": 8},
                    "holidays": [{"name": "Founders Day", "date": "2026-09-01"}],
                }
            },
        )
        assert step3_resp.status_code == 200, step3_resp.text

        ww_after_s3 = await async_client.get(
            "/api/v1/leave/config/work-week", headers=owner_headers
        )
        assert ww_after_s3.json()["weekend_days"] == ["Sunday"]

        # 4. Leave Types CRUD + duplicate 409 + hard delete
        types_resp = await async_client.get("/api/v1/leave/config/types", headers=emp_headers)
        assert types_resp.status_code == 200
        by_code = {t["code"]: t for t in types_resp.json()}
        assert {"annual", "sick", "casual", "unpaid"}.issubset(by_code.keys())
        assert Decimal(str(by_code["annual"]["annual_allowance_days"])) == Decimal("24.00")
        assert Decimal(str(by_code["annual"]["carry_forward_cap_days"])) == Decimal("8.00")

        create_lt = await async_client.post(
            "/api/v1/leave/config/types",
            headers=owner_headers,
            json={
                "code": "study",
                "name": "Study Leave",
                "annual_allowance_days": "5.00",
                "accrual_mode": "monthly",
            },
        )
        assert create_lt.status_code == 201, create_lt.text
        study_id = create_lt.json()["id"]

        dup_lt = await async_client.post(
            "/api/v1/leave/config/types",
            headers=owner_headers,
            json={"code": "STUDY", "name": "Duplicate Study"},
        )
        assert dup_lt.status_code == 409

        patch_lt = await async_client.patch(
            f"/api/v1/leave/config/types/{study_id}",
            headers=owner_headers,
            json={"name": "Exam & Study Leave"},
        )
        assert patch_lt.status_code == 200
        assert patch_lt.json()["name"] == "Exam & Study Leave"

        del_lt = await async_client.delete(
            f"/api/v1/leave/config/types/{study_id}",
            headers=owner_headers,
        )
        assert del_lt.status_code == 200
        assert del_lt.json()["action"] == "deleted"

        # 5. Holidays CRUD + default PK seeding
        hol_list = await async_client.get(
            "/api/v1/leave/config/holidays",
            headers=emp_headers,
            params={"year": 2026},
        )
        assert hol_list.status_code == 200
        hol_dates = {h["holiday_date"] for h in hol_list.json()}
        assert "2026-08-14" in hol_dates
        assert "2026-09-01" in hol_dates

        create_hol = await async_client.post(
            "/api/v1/leave/config/holidays",
            headers=owner_headers,
            json={"holiday_date": "2026-10-15", "name": "Company Retreat", "is_optional": True},
        )
        assert create_hol.status_code == 201
        hol_id = create_hol.json()["id"]

        dup_hol = await async_client.post(
            "/api/v1/leave/config/holidays",
            headers=owner_headers,
            json={"holiday_date": "2026-10-15", "name": "Dup"},
        )
        assert dup_hol.status_code == 409

        upd_hol = await async_client.patch(
            f"/api/v1/leave/config/holidays/{hol_id}",
            headers=owner_headers,
            json={"name": "Annual Company Retreat"},
        )
        assert upd_hol.status_code == 200
        assert upd_hol.json()["name"] == "Annual Company Retreat"

        del_hol = await async_client.delete(
            f"/api/v1/leave/config/holidays/{hol_id}",
            headers=owner_headers,
        )
        assert del_hol.status_code == 200
    finally:
        await _cleanup_globex_stage6(globex.tenant_id)
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "DELETE FROM public.onboarding_state WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
        finally:
            await conn.close()


# ==============================================================================
# 5. Auto-Balance & `leave_ledger` Creation on Employee Create & Excel Import
# ==============================================================================
@pytest.mark.asyncio
async def test_05_auto_balances_and_ledger_on_employee_create_and_import(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - Creating an employee via POST /api/v1/employees automatically creates `leave_balances`
      and `leave_ledger` (`entry_type='accrual'`) rows in the same transaction.
    - `accrual_mode='upfront'` credits `annual_allowance_days`; `accrual_mode='monthly'`
      initializes `0.00` (with a `0.00` accrual ledger row) so monthly jobs never double-allocate.
    - Re-running `initialize_employee_leave_balances` is strictly idempotent.
    - Excel bulk import (`validate` + `commit`) creates balances & ledger rows for all imported staff.
    """
    globex = isolation_context.company_b
    await _cleanup_globex_stage6(globex.tenant_id)
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    created_emp_ids: list[str] = []
    dept_id: str | None = None

    try:
        # Create a monthly-accrual leave type alongside defaults
        await async_client.get("/api/v1/leave/config/types", headers=owner_headers)
        monthly_resp = await async_client.post(
            "/api/v1/leave/config/types",
            headers=owner_headers,
            json={
                "code": "wellness",
                "name": "Wellness Leave",
                "annual_allowance_days": "12.00",
                "accrual_mode": "monthly",
            },
        )
        assert monthly_resp.status_code == 201, monthly_resp.text

        # Create a department for Excel import
        dept_resp = await async_client.post(
            "/api/v1/departments",
            headers=owner_headers,
            json={"name": f"Stage6 Dept {uuid.uuid4().hex[:5]}", "code": "S6D"},
        )
        assert dept_resp.status_code == 201, dept_resp.text
        dept_id = dept_resp.json()["id"]
        dept_name = dept_resp.json()["name"]

        # 1. Create employee via POST /api/v1/employees
        emp_email = f"s6-api-{uuid.uuid4().hex[:6]}@globex.internal"
        emp_resp = await async_client.post(
            "/api/v1/employees",
            headers=owner_headers,
            json={
                "full_name": "Zainab Tariq",
                "email": emp_email,
                "department_id": dept_id,
                "job_title": "QA Engineer",
                "employment_type": "full_time",
                "join_date": "2026-01-15",
            },
        )
        assert emp_resp.status_code == 201, emp_resp.text
        api_emp_id = emp_resp.json()["id"]
        created_emp_ids.append(api_emp_id)

        # Verify balances & ledger rows in DB
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            bal_rows = await conn.fetch(
                """
                SELECT lt.code, lt.accrual_mode, lb.allocated_days, lb.used_days
                FROM public.leave_balances lb
                JOIN public.leave_types lt ON lt.id = lb.leave_type_id
                WHERE lb.tenant_id = $1::uuid AND lb.employee_id = $2::uuid;
                """,
                globex.tenant_id,
                api_emp_id,
            )
            by_code = {r["code"]: r for r in bal_rows}
            assert set(by_code.keys()) == {"annual", "sick", "casual", "unpaid", "wellness"}
            assert Decimal(str(by_code["annual"]["allocated_days"])) == Decimal("20.00")
            assert Decimal(str(by_code["wellness"]["allocated_days"])) == Decimal("0.00")

            ledger_rows = await conn.fetch(
                """
                SELECT entry_type, delta_allocated
                FROM public.leave_ledger
                WHERE tenant_id = $1::uuid AND employee_id = $2::uuid;
                """,
                globex.tenant_id,
                api_emp_id,
            )
            assert len(ledger_rows) == 5
            assert all(r["entry_type"] == "accrual" for r in ledger_rows)
        finally:
            await conn.close()

        # Verify idempotency when initialize_employee_leave_balances is called again
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.clerk_user_id)
            second_count = await initialize_employee_leave_balances(
                db,
                tenant_id=globex.tenant_id,
                employee_id=api_emp_id,
                actor_id=globex.owner.user_id,
            )
            assert second_count == 0
            await db.commit()

        # 2. Excel Bulk Import -> validate + commit
        imp_email_1 = f"s6-imp1-{uuid.uuid4().hex[:6]}@globex.internal"
        imp_email_2 = f"s6-imp2-{uuid.uuid4().hex[:6]}@globex.internal"
        xlsx_bytes = _build_import_xlsx(
            [
                [
                    "Hamza Raza",
                    imp_email_1,
                    "",
                    dept_name,
                    "",
                    "Backend Dev",
                    "full_time",
                    "2026-02-01",
                    "",
                    "",
                ],
                [
                    "Ayesha Noor",
                    imp_email_2,
                    "",
                    dept_name,
                    imp_email_1,
                    "Frontend Dev",
                    "full_time",
                    "2026-02-05",
                    "",
                    "",
                ],
            ]
        )

        val_resp = await async_client.post(
            "/api/v1/employees/import/validate",
            headers=owner_headers,
            files={
                "file": (
                    "stage6_import.xlsx",
                    xlsx_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert val_resp.status_code == 200, val_resp.text
        batch_id = val_resp.json()["import_batch_id"]
        assert val_resp.json()["can_commit"] is True

        com_resp = await async_client.post(
            f"/api/v1/employees/import/{batch_id}/commit",
            headers=owner_headers,
        )
        assert com_resp.status_code == 200, com_resp.text
        imported_ids = com_resp.json()["employee_ids"]
        created_emp_ids.extend(imported_ids)
        assert len(imported_ids) == 2

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            for emp_id in imported_ids:
                bal_cnt = await conn.fetchval(
                    "SELECT count(*) FROM public.leave_balances WHERE tenant_id = $1::uuid AND employee_id = $2::uuid;",
                    globex.tenant_id,
                    emp_id,
                )
                led_cnt = await conn.fetchval(
                    "SELECT count(*) FROM public.leave_ledger WHERE tenant_id = $1::uuid AND employee_id = $2::uuid AND entry_type = 'accrual';",
                    globex.tenant_id,
                    emp_id,
                )
                assert bal_cnt == 5
                assert led_cnt == 5
        finally:
            await conn.close()
    finally:
        await _cleanup_globex_stage6(globex.tenant_id)
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            if created_emp_ids:
                await conn.execute(
                    "DELETE FROM public.employees WHERE id = ANY($1::uuid[]);",
                    created_emp_ids,
                )
            if dept_id:
                await conn.execute(
                    "DELETE FROM public.departments WHERE id = $1::uuid;",
                    dept_id,
                )
            await conn.execute(
                "DELETE FROM public.sheets_sync_queue WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
        finally:
            await conn.close()
