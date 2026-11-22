"""
Stage 6 Prompt 4 Verification Suite:
- Employee self check-in / check-out & `work_week.check_in_enabled` toggle + `GET /me/today`
- Manager daily grid (direct-report RBAC vs HR/Owner) & Two-Phase Excel Attendance Import
- Monthly auto-fill (approved leave with `leave_request_id`, PK holidays, weekends)
- Month completeness review, 409 `ATTENDANCE_GAPS_EXIST` lock refusal, and atomic month lock
- Post-lock direct edit 409 blocks & Stage 2 `attendance.post_lock_edit` approval + before/after audit
- Google Sheets `Attendance` & `Leave Tracker` deduped queue rows
- 3-month deterministic attendance seed & `pub_attendance` Forced-RLS cross-tenant isolation
"""

import calendar
import io
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

import asyncpg
import httpx
import openpyxl
import pytest
import pytest_asyncio

from app.db.session import AsyncSessionLocal
from app.models.tenant import set_tenant_context
from app.services.attendance_seed import seed_three_months_attendance
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    MultiTenantTestContext,
    get_app_connection,
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


async def _setup_globex_attendance_employees(
    *,
    tenant_id: str,
    employee_user_id: str,
    manager_user_id: str,
    owner_user_id: str,
) -> dict[str, str]:
    """Creates clean Globex departments, 3 linked employees (emp -> mgr -> owner), and leave config."""
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "sheets_sync_queue",
            "outbox_events",
            "notifications",
            "attendance",
            "attendance_month_locks",
            "leave_requests",
            "leave_ledger",
            "leave_balances",
            "leave_types",
            "holidays",
            "work_week",
            "employees",
            "departments",
        ):
            await conn.execute(f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;", tenant_id)
        await conn.execute(
            "DELETE FROM public.approvals WHERE tenant_id = $1::uuid "
            "AND approval_type IN ('leave.exception', 'attendance.post_lock_edit');",
            tenant_id,
        )

        dept_id = await conn.fetchval(
            "SELECT id FROM public.departments WHERE tenant_id = $1::uuid AND name = 'Engineering';",
            tenant_id,
        )
        if dept_id is None:
            dept_id = await conn.fetchval(
                """
                INSERT INTO public.departments (tenant_id, name, code)
                VALUES ($1::uuid, 'Engineering', 'ENG')
                RETURNING id;
                """,
                tenant_id,
            )
        owner_emp_id = await conn.fetchval(
            """
            INSERT INTO public.employees (
                tenant_id, user_id, employee_code, full_name, email,
                department_id, job_title, employment_type, join_date, status
            )
            VALUES (
                $1::uuid, $2::uuid, 'GLX-001', 'Globex Owner',
                'globex.owner@example.com', $3::uuid, 'CEO', 'full_time', '2025-01-01', 'active'
            )
            RETURNING id;
            """,
            tenant_id,
            owner_user_id,
            dept_id,
        )
        mgr_emp_id = await conn.fetchval(
            """
            INSERT INTO public.employees (
                tenant_id, user_id, employee_code, full_name, email,
                department_id, manager_id, job_title, employment_type, join_date, status
            )
            VALUES (
                $1::uuid, $2::uuid, 'GLX-002', 'Globex Manager',
                'globex.manager@example.com', $3::uuid, $4::uuid,
                'Engineering Manager', 'full_time', '2025-01-01', 'active'
            )
            RETURNING id;
            """,
            tenant_id,
            manager_user_id,
            dept_id,
            owner_emp_id,
        )
        emp_id = await conn.fetchval(
            """
            INSERT INTO public.employees (
                tenant_id, user_id, employee_code, full_name, email,
                department_id, manager_id, job_title, employment_type, join_date, status
            )
            VALUES (
                $1::uuid, $2::uuid, 'GLX-003', 'Globex Engineer',
                'globex.employee@example.com', $3::uuid, $4::uuid,
                'Software Engineer', 'full_time', '2025-01-01', 'active'
            )
            RETURNING id;
            """,
            tenant_id,
            employee_user_id,
            dept_id,
            mgr_emp_id,
        )
        return {
            "dept_id": str(dept_id),
            "owner_emp_id": str(owner_emp_id),
            "mgr_emp_id": str(mgr_emp_id),
            "emp_id": str(emp_id),
        }
    finally:
        await conn.close()


async def _teardown_globex_attendance(tenant_id: str) -> None:
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "sheets_sync_queue",
            "outbox_events",
            "notifications",
            "attendance",
            "attendance_month_locks",
            "leave_requests",
            "leave_ledger",
            "leave_balances",
            "leave_types",
            "holidays",
            "work_week",
            "employees",
            "departments",
        ):
            await conn.execute(f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;", tenant_id)
        await conn.execute(
            "DELETE FROM public.approvals WHERE tenant_id = $1::uuid "
            "AND approval_type IN ('leave.exception', 'attendance.post_lock_edit');",
            tenant_id,
        )
    finally:
        await conn.close()


def _build_attendance_xlsx(rows: list[list[Any]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["employee_email", "date", "status", "notes"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    try:
        wb.save(buf)
    finally:
        wb.close()
    return buf.getvalue()


# ==============================================================================
# 1. Self Check-In Toggle, Check-Out & Same-Day Idempotency
# ==============================================================================
@pytest.mark.asyncio
async def test_01_self_check_in_toggle_and_same_day_idempotency(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    await _setup_globex_attendance_employees(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        # 1. GET /me/today returns default check_in_enabled=True, unlocked, record=None
        today_resp = await async_client.get("/api/v1/attendance/me/today", headers=emp_headers)
        assert today_resp.status_code == 200, today_resp.text
        today_data = today_resp.json()
        assert today_data["check_in_enabled"] is True
        assert today_data["is_locked"] is False
        assert today_data["record"] is None

        # 2. Disable check_in_enabled -> POST /check-in returns 403 CHECK_IN_DISABLED
        ww_off = await async_client.put(
            "/api/v1/leave/config/work-week",
            headers=owner_headers,
            json={"check_in_enabled": False},
        )
        assert ww_off.status_code == 200, ww_off.text

        blocked_ci = await async_client.post(
            "/api/v1/attendance/check-in",
            headers=emp_headers,
            json={"notes": "Should fail"},
        )
        assert blocked_ci.status_code == 403
        assert "CHECK_IN_DISABLED" in blocked_ci.text

        # 3. Re-enable check_in_enabled -> Check-in succeeds
        ww_on = await async_client.put(
            "/api/v1/leave/config/work-week",
            headers=owner_headers,
            json={"check_in_enabled": True},
        )
        assert ww_on.status_code == 200

        ci1 = await async_client.post(
            "/api/v1/attendance/check-in",
            headers=emp_headers,
            json={"notes": "Arrived at office"},
        )
        assert ci1.status_code == 200, ci1.text
        rec1 = ci1.json()
        assert rec1["status"] == "PRESENT"
        assert rec1["source"] == "check_in"
        assert rec1["check_in_at"] is not None
        assert rec1["check_out_at"] is None

        # 4. Repeat same-day check-out -> updates same row idempotently
        ci2 = await async_client.post(
            "/api/v1/attendance/check-in",
            headers=emp_headers,
            json={"action": "check_out", "check_out": True, "notes": "Leaving"},
        )
        assert ci2.status_code == 200, ci2.text
        rec2 = ci2.json()
        assert rec2["id"] == rec1["id"]
        assert rec2["check_in_at"] == rec1["check_in_at"]
        assert rec2["check_out_at"] is not None

        today_after = await async_client.get("/api/v1/attendance/me/today", headers=emp_headers)
        assert today_after.status_code == 200
        assert today_after.json()["record"]["id"] == rec1["id"]
    finally:
        await _teardown_globex_attendance(globex.tenant_id)


# ==============================================================================
# 2. Manager Daily Grid & Two-Phase Excel Attendance Import
# ==============================================================================
@pytest.mark.asyncio
async def test_02_manager_daily_grid_and_excel_import_two_phase(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    ids = await _setup_globex_attendance_employees(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        # Demote globex.manager is_hr_head=false to test plain-manager direct-report scoping
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
        finally:
            await conn.close()

        # 1. Plain manager sees only direct reports in GET /daily
        grid_get = await async_client.get(
            "/api/v1/attendance/daily",
            headers=mgr_headers,
            params={"target_date": "2026-08-03"},
        )
        assert grid_get.status_code == 200, grid_get.text
        assert grid_get.json()["total_employees"] == 1
        assert grid_get.json()["rows"][0]["employee_id"] == ids["emp_id"]

        # 2. Plain manager can mark direct report WFH, but gets 403 for non-direct-report (owner)
        grid_ok = await async_client.put(
            "/api/v1/attendance/daily",
            headers=mgr_headers,
            json={
                "target_date": "2026-08-03",
                "entries": [{"employee_id": ids["emp_id"], "status": "WFH", "notes": "Remote Mon"}],
            },
        )
        assert grid_ok.status_code == 200, grid_ok.text
        assert grid_ok.json()["upserted_count"] == 1
        assert grid_ok.json()["records"][0]["source"] == "manager_grid"

        grid_forb = await async_client.put(
            "/api/v1/attendance/daily",
            headers=mgr_headers,
            json={
                "target_date": "2026-08-03",
                "entries": [{"employee_id": ids["owner_emp_id"], "status": "PRESENT"}],
            },
        )
        assert grid_forb.status_code == 403

        # 3. Plain manager cannot call HR-only Excel import -> 403
        spoof_resp_mgr = await async_client.post(
            "/api/v1/attendance/import/validate",
            headers=mgr_headers,
            files={"file": ("att.xlsx", b"MZ\x90\x00spoof", "application/octet-stream")},
        )
        assert spoof_resp_mgr.status_code == 403

        # 4. Owner spoofed .xlsx rejected with 400 INVALID_XLSX_FILE
        spoof_resp = await async_client.post(
            "/api/v1/attendance/import/validate",
            headers=owner_headers,
            files={"file": ("att.xlsx", b"MZ\x90\x00spoof", "application/octet-stream")},
        )
        assert spoof_resp.status_code == 400
        assert "INVALID_XLSX_FILE" in spoof_resp.text

        # 5. Mixed invalid + valid rows -> detailed error report, commit blocked (400)
        bad_xlsx = _build_attendance_xlsx(
            [
                ["globex.employee@example.com", date(2026, 8, 4), "PRESENT", "Native date"],
                ["missing.person@example.com", "2026-08-04", "TELEPORT", "Bad row"],
            ]
        )
        val_bad = await async_client.post(
            "/api/v1/attendance/import/validate",
            headers=owner_headers,
            files={
                "file": (
                    "bad_att.xlsx",
                    bad_xlsx,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert val_bad.status_code == 200, val_bad.text
        bad_data = val_bad.json()
        assert bad_data["valid_rows"] == 1
        assert bad_data["invalid_rows"] == 1
        assert bad_data["can_commit"] is False
        assert len(bad_data["errors"]) >= 2

        commit_bad = await async_client.post(
            f"/api/v1/attendance/import/{bad_data['import_batch_id']}/commit",
            headers=owner_headers,
        )
        assert commit_bad.status_code == 400
        assert "IMPORT_BATCH_HAS_ERRORS" in commit_bad.text

        # 6. Clean workbook with native datetime/date + ISO string -> validates & commits
        good_xlsx = _build_attendance_xlsx(
            [
                ["globex.employee@example.com", datetime(2026, 8, 4, 0, 0), "PRESENT", "In office"],
                ["globex.employee@example.com", "2026-08-05", "HALF_DAY", "Morning only"],
            ]
        )
        val_good = await async_client.post(
            "/api/v1/attendance/import/validate",
            headers=owner_headers,
            files={
                "file": (
                    "good_att.xlsx",
                    good_xlsx,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert val_good.status_code == 200, val_good.text
        good_data = val_good.json()
        assert good_data["valid_rows"] == 2
        assert good_data["invalid_rows"] == 0
        assert good_data["can_commit"] is True

        commit_good = await async_client.post(
            f"/api/v1/attendance/import/{good_data['import_batch_id']}/commit",
            headers=owner_headers,
        )
        assert commit_good.status_code == 200, commit_good.text
        assert commit_good.json()["committed_count"] == 2
        assert commit_good.json()["sheets_queued"] == 2
    finally:
        await _teardown_globex_attendance(globex.tenant_id)


# ==============================================================================
# 3. Auto-Fill, Month Review Gaps & Gap-Checked Month Lock
# ==============================================================================
@pytest.mark.asyncio
async def test_03_auto_fill_month_review_gaps_and_lock_month_governance(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    ids = await _setup_globex_attendance_employees(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        # 1. Seed leave types & relax max_team_absent_pct (1/3 = 33.3% > default 30%)
        lt_resp = await async_client.get("/api/v1/leave/config/types", headers=owner_headers)
        assert lt_resp.status_code == 200
        ww_resp = await async_client.put(
            "/api/v1/leave/config/work-week",
            headers=owner_headers,
            json={"max_team_absent_pct": "50.00"},
        )
        assert ww_resp.status_code == 200, ww_resp.text
        # Ensure employee balance exists for 2026
        bal_resp = await async_client.get(
            "/api/v1/leave/balances/me", headers=emp_headers, params={"year": 2026}
        )
        assert bal_resp.status_code == 200

        leave_sub = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-17",
                "end_date": "2026-08-18",
                "reason": "August trip",
                "as_of_date": "2026-08-01",
                "start_workflow": False,
            },
        )
        assert leave_sub.status_code == 201, leave_sub.text
        leave_req_id = leave_sub.json()["id"]

        leave_dec = await async_client.post(
            f"/api/v1/leave/requests/{leave_req_id}/decide",
            headers=owner_headers,
            json={"decision": "approved", "note": "Approved"},
        )
        assert leave_dec.status_code == 200, leave_dec.text
        assert leave_dec.json()["status"] == "approved"

        # Delete the Aug 17-18 attendance rows temporarily to verify auto_fill_month populates
        # ON_LEAVE with leave_request_id AND never overwrites manual PRESENT on Aug 3
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "DELETE FROM public.attendance WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
            await conn.execute(
                """
                INSERT INTO public.attendance (tenant_id, employee_id, attendance_date, status, source)
                VALUES ($1::uuid, $2::uuid, $3, 'PRESENT', 'manager_grid');
                """,
                globex.tenant_id,
                ids["emp_id"],
                date(2026, 8, 3),
            )
        finally:
            await conn.close()

        # 2. Auto-fill 2026-08
        af_resp = await async_client.post(
            "/api/v1/attendance/months/2026-08/auto-fill",
            headers=owner_headers,
        )
        assert af_resp.status_code == 200, af_resp.text
        af_data = af_resp.json()
        assert af_data["active_employees"] == 3
        assert af_data["filled_leave_days"] == 2
        assert af_data["filled_holiday_days"] >= 3  # Aug 14 Independence Day for 3 employees
        assert af_data["filled_weekend_days"] == 30  # 10 Sat/Sun in Aug 2026 * 3 employees

        # Verify manual PRESENT on Aug 3 preserved & ON_LEAVE has leave_request_id
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            aug3 = await conn.fetchrow(
                """
                SELECT status, source FROM public.attendance
                WHERE tenant_id = $1::uuid AND employee_id = $2::uuid AND attendance_date = $3;
                """,
                globex.tenant_id,
                ids["emp_id"],
                date(2026, 8, 3),
            )
            assert aug3["status"] == "PRESENT"
            assert aug3["source"] == "manager_grid"

            aug17 = await conn.fetchrow(
                """
                SELECT status, source, leave_request_id FROM public.attendance
                WHERE tenant_id = $1::uuid AND employee_id = $2::uuid AND attendance_date = $3;
                """,
                globex.tenant_id,
                ids["emp_id"],
                date(2026, 8, 17),
            )
            assert aug17["status"] == "ON_LEAVE"
            assert str(aug17["leave_request_id"]) == leave_req_id
        finally:
            await conn.close()

        # 3. Month Review shows remaining gaps -> Lock refused with 409 ATTENDANCE_GAPS_EXIST
        rev1 = await async_client.get(
            "/api/v1/attendance/months/2026-08/review", headers=owner_headers
        )
        assert rev1.status_code == 200, rev1.text
        assert rev1.json()["total_gaps"] > 0
        assert rev1.json()["can_lock"] is False

        lock_fail = await async_client.post(
            "/api/v1/attendance/months/2026-08/lock", headers=owner_headers
        )
        assert lock_fail.status_code == 409
        assert "ATTENDANCE_GAPS_EXIST" in lock_fail.text

        # 4. Fill all remaining missing days via daily grid -> review shows 0 gaps -> lock succeeds
        for emp_item in rev1.json()["employees"]:
            for missing_d in emp_item["missing_days"]:
                up_resp = await async_client.put(
                    "/api/v1/attendance/daily",
                    headers=owner_headers,
                    json={
                        "target_date": missing_d,
                        "entries": [{"employee_id": emp_item["employee_id"], "status": "PRESENT"}],
                    },
                )
                assert up_resp.status_code == 200, up_resp.text

        rev2 = await async_client.get(
            "/api/v1/attendance/months/2026-08/review", headers=owner_headers
        )
        assert rev2.status_code == 200
        assert rev2.json()["total_gaps"] == 0
        assert rev2.json()["can_lock"] is True

        lock_ok = await async_client.post(
            "/api/v1/attendance/months/2026-08/lock", headers=owner_headers
        )
        assert lock_ok.status_code == 200, lock_ok.text
        assert lock_ok.json()["year_month"] == "2026-08"
        assert lock_ok.json()["employee_count"] == 3
        assert lock_ok.json()["record_count"] == 3 * 31

        # Verify DB state: attendance.locked=True, outbox attendance.month_locked, Sheets queue
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            unlocked_cnt = await conn.fetchval(
                """
                SELECT count(*) FROM public.attendance
                WHERE tenant_id = $1::uuid AND attendance_date >= '2026-08-01'
                  AND attendance_date <= '2026-08-31' AND locked = false;
                """,
                globex.tenant_id,
            )
            assert unlocked_cnt == 0

            outbox_cnt = await conn.fetchval(
                """
                SELECT count(*) FROM public.outbox_events
                WHERE tenant_id = $1::uuid AND event_type = 'attendance.month_locked';
                """,
                globex.tenant_id,
            )
            assert outbox_cnt == 1

            leave_sheet_cnt = await conn.fetchval(
                """
                SELECT count(*) FROM public.sheets_sync_queue
                WHERE tenant_id = $1::uuid AND sheet_name = 'Leave Tracker' AND entity_type = 'leave_request';
                """,
                globex.tenant_id,
            )
            assert leave_sheet_cnt == 1

            att_sheet_lock_cnt = await conn.fetchval(
                """
                SELECT count(*) FROM public.sheets_sync_queue
                WHERE tenant_id = $1::uuid AND sheet_name = 'Attendance' AND action = 'month_lock';
                """,
                globex.tenant_id,
            )
            assert att_sheet_lock_cnt == 1
        finally:
            await conn.close()
    finally:
        await _teardown_globex_attendance(globex.tenant_id)


# ==============================================================================
# 4. Post-Lock Direct Edit Blocked + Stage 2 `attendance.post_lock_edit` Hook
# ==============================================================================
@pytest.mark.asyncio
async def test_04_post_lock_direct_edits_blocked_and_stage2_approval_applies_change(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    ids = await _setup_globex_attendance_employees(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        # Seed 2026-08 and lock it cleanly
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.user_id)
            await seed_three_months_attendance(
                db,
                tenant_id=globex.tenant_id,
                year_months=["2026-08"],
                actor_id=globex.owner.user_id,
            )
            await db.commit()

        lock_resp = await async_client.post(
            "/api/v1/attendance/months/2026-08/lock", headers=owner_headers
        )
        assert lock_resp.status_code == 200, lock_resp.text

        # 1. Direct edits to locked 2026-08 are blocked with 409
        ci_locked = await async_client.post(
            "/api/v1/attendance/check-in",
            headers=emp_headers,
            json={"target_date": "2026-08-03"},
        )
        assert ci_locked.status_code == 409
        assert "ATTENDANCE_MONTH_LOCKED" in ci_locked.text

        grid_locked = await async_client.put(
            "/api/v1/attendance/daily",
            headers=owner_headers,
            json={
                "target_date": "2026-08-03",
                "entries": [{"employee_id": ids["emp_id"], "status": "WFH"}],
            },
        )
        assert grid_locked.status_code == 409

        af_locked = await async_client.post(
            "/api/v1/attendance/months/2026-08/auto-fill",
            headers=owner_headers,
        )
        assert af_locked.status_code == 409

        xlsx_locked = _build_attendance_xlsx(
            [["globex.employee@example.com", "2026-08-03", "WFH", "Should fail"]]
        )
        val_locked = await async_client.post(
            "/api/v1/attendance/import/validate",
            headers=owner_headers,
            files={
                "file": (
                    "locked.xlsx",
                    xlsx_locked,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert val_locked.status_code == 200
        assert val_locked.json()["invalid_rows"] == 1
        assert val_locked.json()["can_commit"] is False

        # 2. Manager submits Stage 2 `attendance.post_lock_edit` approval request
        req_edit = await async_client.post(
            "/api/v1/attendance/post-lock-edit",
            headers=mgr_headers,
            json={
                "employee_id": ids["emp_id"],
                "attendance_date": "2026-08-03",
                "new_status": "ABSENT",
                "notes": "Retroactive payroll correction",
                "reason": "Employee confirmed unpaid absence on Aug 3",
                "approver_role": "hr_head",
            },
        )
        assert req_edit.status_code == 201, req_edit.text
        edit_data = req_edit.json()
        approval_id = edit_data["approval_id"]
        assert edit_data["approval_status"] == "pending"

        # 3. Owner approves Stage 2 approval -> atomically mutates locked row + before/after audit
        app_resp = await async_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=owner_headers,
            json={"reason": "Verified with team lead"},
        )
        assert app_resp.status_code == 200, app_resp.text
        assert app_resp.json()["status"] == "approved"

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            att_row = await conn.fetchrow(
                """
                SELECT id, status, source, notes, locked
                FROM public.attendance
                WHERE tenant_id = $1::uuid AND employee_id = $2::uuid AND attendance_date = $3;
                """,
                globex.tenant_id,
                ids["emp_id"],
                date(2026, 8, 3),
            )
            assert att_row["status"] == "ABSENT"
            assert att_row["source"] == "post_lock_edit"
            assert att_row["notes"] == "Retroactive payroll correction"
            assert att_row["locked"] is True

            audit_row = await conn.fetchrow(
                """
                SELECT details FROM public.audit_logs
                WHERE tenant_id = $1::uuid AND action = 'attendance.post_lock_edited'
                  AND target_id = $2;
                """,
                globex.tenant_id,
                str(att_row["id"]),
            )
            assert audit_row is not None
            details = audit_row["details"]
            if isinstance(details, str):
                import json

                details = json.loads(details)
            assert details["after"]["status"] == "ABSENT"
            assert details["after"]["locked"] is True
            assert details["before"]["status"] is not None

            sheet_cnt = await conn.fetchval(
                """
                SELECT count(*) FROM public.sheets_sync_queue
                WHERE tenant_id = $1::uuid AND sheet_name = 'Attendance' AND action = 'post_lock_edit';
                """,
                globex.tenant_id,
            )
            assert sheet_cnt == 1
        finally:
            await conn.close()
    finally:
        await _teardown_globex_attendance(globex.tenant_id)


# ==============================================================================
# 5. 3-Month Deterministic Seed & `pub_attendance` Cross-Tenant Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_05_three_month_seed_and_pub_attendance_rls_isolation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    acme = isolation_context.company_a
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    await _setup_globex_attendance_employees(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.user_id)
            seed_summary = await seed_three_months_attendance(
                db,
                tenant_id=globex.tenant_id,
                reference_date=date(2026, 8, 15),
                actor_id=globex.owner.user_id,
            )
            await db.commit()

        assert seed_summary["year_months"] == ["2026-06", "2026-07", "2026-08"]
        assert seed_summary["employees_processed"] == 3
        expected_days = (
            calendar.monthrange(2026, 6)[1]
            + calendar.monthrange(2026, 7)[1]
            + calendar.monthrange(2026, 8)[1]
        )
        assert seed_summary["inserted_rows"] == 3 * expected_days

        # Every seeded month passes Month Review with zero gaps
        for ym in ("2026-06", "2026-07", "2026-08"):
            rev = await async_client.get(
                f"/api/v1/attendance/months/{ym}/review", headers=owner_headers
            )
            assert rev.status_code == 200, rev.text
            assert rev.json()["total_gaps"] == 0
            assert rev.json()["can_lock"] is True

        # Forced-RLS check on `public.pub_attendance` as runtime role `agentic_hr_app`
        app_conn = await get_app_connection()
        try:
            # Uncontexted -> 0 rows
            assert await app_conn.fetchval("SELECT count(*) FROM public.pub_attendance;") == 0

            # Globex context -> sees all 3 months of Globex rows
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    globex.tenant_id,
                )
                globex_cnt = await app_conn.fetchval("SELECT count(*) FROM public.pub_attendance;")
                assert globex_cnt == 3 * expected_days

            # Acme context -> 0 Globex rows
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    acme.tenant_id,
                )
                acme_cnt = await app_conn.fetchval(
                    "SELECT count(*) FROM public.pub_attendance WHERE tenant_id = $1::uuid;",
                    uuid.UUID(globex.tenant_id),
                )
                assert acme_cnt == 0
        finally:
            await app_conn.close()
    finally:
        await _teardown_globex_attendance(globex.tenant_id)
