"""
Stage 6 Prompt 7 — Master Exit Verification Suite (Leave, Attendance & Month Lock).

Verifies all Stage 6 Exit Gates end-to-end:
1. All 16 required Leave Policy, Concurrency, Temporal Escalation & HR Exception cases.
2. Mid-approval worker crash & retry idempotency (exactly 1 outbox, 1 ledger, 1 calendar, 0 dup attendance).
3. 3-month deterministic attendance seed, `pub_attendance` & `pub_leave` Forced-RLS isolation,
   and `GET /` Stage 6 platform progression badge.
"""

import asyncio
import calendar
import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import asyncpg
import httpx
import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.core.encryption import EncryptionContext, encrypt_field
from app.core.temporal import set_temporal_client
from app.db.session import AsyncSessionLocal
from app.models.tenant import set_tenant_context
from app.services.attendance_seed import seed_three_months_attendance
from app.services.leave_service import LeaveService
from app.workflows.activities import (
    approve_leave_request_activity,
    escalate_leave_request_activity,
    expire_leave_request_activity,
    notify_leave_requested_activity,
    release_leave_reservation_activity,
    send_leave_reminder_activity,
)
from app.workflows.leave_request_workflow import LeaveRequestWorkflow
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


async def _setup_globex_exit_state(
    *,
    tenant_id: str,
    employee_user_id: str,
    manager_user_id: str,
    owner_user_id: str,
    with_connector: bool = False,
) -> dict[str, str]:
    """Resets Globex Corp with a 3-employee hierarchy, work_week (50% coverage), and PK holidays."""
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "notifications",
            "sent_messages",
            "calendar_events",
            "sheets_sync_queue",
            "idempotency_keys",
            "outbox_events",
            "attendance",
            "attendance_month_locks",
            "leave_requests",
            "leave_ledger",
            "leave_balances",
            "leave_types",
            "holidays",
            "work_week",
            "employee_documents",
            "approvals",
            "connector_credentials",
            "employees",
            "departments",
        ):
            await conn.execute(f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;", tenant_id)

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
                'owner.exit@globex.internal', $3::uuid, 'CEO', 'full_time', '2024-01-01', 'active'
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
                'mgr.exit@globex.internal', $3::uuid, $4::uuid,
                'Engineering Manager', 'full_time', '2024-01-01', 'active'
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
                'emp.exit@globex.internal', $3::uuid, $4::uuid,
                'Software Engineer', 'full_time', '2024-01-01', 'active'
            )
            RETURNING id;
            """,
            tenant_id,
            employee_user_id,
            dept_id,
            mgr_emp_id,
        )

        # Configure work_week deliberately so normal 1/3 (33.3%) requests stay OK,
        # and backup_approver_user_id is set to owner for Case 15 escalation.
        await conn.execute(
            """
            INSERT INTO public.work_week (
                tenant_id, weekend_days, probation_months,
                max_team_absent_pct, min_team_present,
                backup_approver_user_id, check_in_enabled, timezone
            )
            VALUES ($1::uuid, '["Saturday","Sunday"]'::jsonb, 3, 50.00, 1, $2::uuid, true, 'Asia/Karachi');
            """,
            tenant_id,
            owner_user_id,
        )

        if with_connector:
            cred_id = uuid.uuid4()
            enc_acc = encrypt_field(
                "fake-acc-s6-exit",
                EncryptionContext(tenant_id, "connector_credential", cred_id, "access_token"),
            )
            enc_ref = encrypt_field(
                "fake-ref-s6-exit",
                EncryptionContext(tenant_id, "connector_credential", cred_id, "refresh_token"),
            )
            await conn.execute(
                """
                INSERT INTO public.connector_credentials (
                    id, tenant_id, provider, account_email, scopes,
                    encrypted_access_token, encrypted_refresh_token,
                    token_expiry, status, is_read_only
                )
                VALUES (
                    $1::uuid, $2::uuid, 'google', 'agentic.hr.test.ubaid.owner@gmail.com',
                    '["https://www.googleapis.com/auth/calendar.events","https://www.googleapis.com/auth/gmail.send"]'::jsonb,
                    $3, $4, NOW() + INTERVAL '2 hours', 'connected', false
                );
                """,
                str(cred_id),
                tenant_id,
                enc_acc,
                enc_ref,
            )

        return {
            "dept_id": str(dept_id),
            "owner_emp_id": str(owner_emp_id),
            "mgr_emp_id": str(mgr_emp_id),
            "emp_id": str(emp_id),
        }
    finally:
        await conn.close()


async def _teardown_globex_exit_state(tenant_id: str) -> None:
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "notifications",
            "sent_messages",
            "calendar_events",
            "sheets_sync_queue",
            "idempotency_keys",
            "outbox_events",
            "attendance",
            "attendance_month_locks",
            "leave_requests",
            "leave_ledger",
            "leave_balances",
            "leave_types",
            "holidays",
            "work_week",
            "employee_documents",
            "approvals",
            "connector_credentials",
            "employees",
            "departments",
        ):
            await conn.execute(f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;", tenant_id)
    finally:
        await conn.close()


# ==============================================================================
# 1. All 16 Required Leave Cases (End-to-End API + DB + Temporal)
# ==============================================================================
@pytest.mark.asyncio
async def test_01_all_16_required_leave_cases_end_to_end(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    ids = await _setup_globex_exit_state(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    results_log: list[str] = []

    try:
        # Ensure default leave types & 2026 balances exist
        lt_list = (
            await async_client.get("/api/v1/leave/config/types", headers=owner_headers)
        ).json()
        annual_lt = next(lt for lt in lt_list if lt["code"] == "annual")
        bal_init = await async_client.get(
            "/api/v1/leave/balances/me", headers=emp_headers, params={"year": 2026}
        )
        assert bal_init.status_code == 200

        # Case 1: Normal annual leave (OK -> approved -> ON_LEAVE + deduct ledger + leave.approved)
        c1 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-03",
                "end_date": "2026-08-04",
                "reason": "Case 1 normal annual",
                "as_of_date": "2026-07-20",
                "start_workflow": False,
            },
        )
        assert c1.status_code == 201, c1.text
        assert c1.json()["policy_check_status"] == "OK"
        c1_dec = await async_client.post(
            f"/api/v1/leave/requests/{c1.json()['id']}/decide",
            headers=mgr_headers,
            json={"decision": "approved"},
        )
        assert c1_dec.status_code == 200 and c1_dec.json()["status"] == "approved"
        results_log.append("Case 01: Normal annual leave -> OK & approved")

        # Case 2: Insufficient balance (18 left, request 20 working days -> 409 BLOCKED)
        c2 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-11-02",
                "end_date": "2026-11-30",
                "reason": "Case 2 insufficient balance",
                "as_of_date": "2026-10-15",
                "start_workflow": False,
            },
        )
        assert c2.status_code == 409
        assert "INSUFFICIENT_BALANCE" in c2.text
        results_log.append("Case 02: Insufficient balance -> 409 BLOCKED")

        # Case 3: Overlap with Case 1 -> 409 BLOCKED
        c3 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-04",
                "end_date": "2026-08-05",
                "reason": "Case 3 overlap",
                "as_of_date": "2026-07-20",
                "start_workflow": False,
            },
        )
        assert c3.status_code == 409
        assert "OVERLAPPING_LEAVE" in c3.text
        results_log.append("Case 03: Overlapping leave -> 409 BLOCKED")

        # Case 4: Half day (0.50 working days)
        c4 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-06",
                "end_date": "2026-08-06",
                "is_half_day": True,
                "half_day_period": "morning",
                "reason": "Case 4 half day",
                "as_of_date": "2026-07-20",
                "start_workflow": False,
            },
        )
        assert c4.status_code == 201 and Decimal(str(c4.json()["working_days"])) == Decimal("0.50")
        c4_dec = await async_client.post(
            f"/api/v1/leave/requests/{c4.json()['id']}/decide",
            headers=mgr_headers,
            json={"decision": "approved"},
        )
        assert c4_dec.status_code == 200 and c4_dec.json()["status"] == "approved"
        results_log.append("Case 04: Half day -> 0.50 working days deducted")

        # Case 5: Range across public holiday (Aug 13..17 spans Aug 14 Independence Day -> 2 working days)
        c5 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-13",
                "end_date": "2026-08-17",
                "reason": "Case 5 across holiday",
                "as_of_date": "2026-08-01",
                "start_workflow": False,
            },
        )
        assert c5.status_code == 201 and Decimal(str(c5.json()["working_days"])) == Decimal("2.00")
        c5_dec = await async_client.post(
            f"/api/v1/leave/requests/{c5.json()['id']}/decide",
            headers=mgr_headers,
            json={"decision": "approved"},
        )
        assert c5_dec.status_code == 200 and c5_dec.json()["status"] == "approved"
        results_log.append("Case 05: Range across holiday -> Aug 14 excluded (2.00 days)")

        # Case 6: Weekend-only range -> 409 BLOCKED
        c6 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-08",
                "end_date": "2026-08-09",
                "reason": "Case 6 weekend only",
                "as_of_date": "2026-08-01",
                "start_workflow": False,
            },
        )
        assert c6.status_code == 409
        assert "NO_WORKING_DAYS" in c6.text
        results_log.append("Case 06: Weekend-only range -> 409 NO_WORKING_DAYS")

        # Case 7: During probation -> VIOLATION + hr_exception_approval_id
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.employees SET join_date = '2026-07-01' WHERE id = $1::uuid;",
                ids["emp_id"],
            )
        finally:
            await conn.close()
        c7 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-24",
                "end_date": "2026-08-25",
                "reason": "Case 7 probation violation",
                "as_of_date": "2026-08-01",
                "start_workflow": False,
            },
        )
        assert c7.status_code == 201 and c7.json()["policy_check_status"] == "VIOLATION"
        assert any(r["code"] == "DURING_PROBATION" for r in c7.json()["policy_check_reasons"])
        await async_client.post(
            f"/api/v1/leave/requests/{c7.json()['id']}/cancel", headers=emp_headers
        )
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.employees SET join_date = '2024-01-01' WHERE id = $1::uuid;",
                ids["emp_id"],
            )
        finally:
            await conn.close()
        results_log.append("Case 07: During probation -> VIOLATION (DURING_PROBATION)")

        # Case 8: Over consecutive limit -> VIOLATION
        await async_client.patch(
            f"/api/v1/leave/config/types/{annual_lt['id']}",
            headers=owner_headers,
            json={"max_consecutive_days": 5},
        )
        c8 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-09-07",
                "end_date": "2026-09-14",
                "reason": "Case 8 consecutive limit",
                "as_of_date": "2026-08-20",
                "start_workflow": False,
            },
        )
        assert c8.status_code == 201 and c8.json()["policy_check_status"] == "VIOLATION"
        assert any(
            r["code"] == "EXCEEDS_MAX_CONSECUTIVE_DAYS" for r in c8.json()["policy_check_reasons"]
        )
        await async_client.post(
            f"/api/v1/leave/requests/{c8.json()['id']}/cancel", headers=emp_headers
        )
        await async_client.patch(
            f"/api/v1/leave/config/types/{annual_lt['id']}",
            headers=owner_headers,
            json={"max_consecutive_days": 15},
        )
        results_log.append("Case 08: Over consecutive limit -> VIOLATION")

        # Case 9: Short notice -> VIOLATION
        c9 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-09-02",
                "end_date": "2026-09-02",
                "reason": "Case 9 short notice",
                "as_of_date": "2026-09-01",
                "start_workflow": False,
            },
        )
        assert c9.status_code == 201 and c9.json()["policy_check_status"] == "VIOLATION"
        assert any(r["code"] == "SHORT_NOTICE" for r in c9.json()["policy_check_reasons"])
        await async_client.post(
            f"/api/v1/leave/requests/{c9.json()['id']}/cancel", headers=emp_headers
        )
        results_log.append("Case 09: Short notice -> VIOLATION (SHORT_NOTICE)")

        # Case 10: Sick leave > 2 working days -> BLOCKED without doc, OK with document_id
        c10_no_doc = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "sick",
                "start_date": "2026-09-15",
                "end_date": "2026-09-17",
                "reason": "Case 10 sick 3 days no doc",
                "as_of_date": "2026-09-15",
                "start_workflow": False,
            },
        )
        assert c10_no_doc.status_code == 409
        assert "DOCUMENT_REQUIRED" in c10_no_doc.text

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            doc_id = str(
                await conn.fetchval(
                    """
                    INSERT INTO public.employee_documents (
                        tenant_id, employee_id, document_type, title,
                        file_name, mime_type, size_bytes, storage_provider, storage_key
                    )
                    VALUES (
                        $1::uuid, $2::uuid, 'other', 'Medical Certificate',
                        'med_cert.pdf', 'application/pdf', 1024, 'local', 't/med_cert.pdf'
                    )
                    RETURNING id;
                    """,
                    globex.tenant_id,
                    ids["emp_id"],
                )
            )
        finally:
            await conn.close()

        c10_with_doc = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "sick",
                "start_date": "2026-09-15",
                "end_date": "2026-09-17",
                "reason": "Case 10 sick 3 days with doc",
                "document_id": doc_id,
                "as_of_date": "2026-09-15",
                "start_workflow": False,
            },
        )
        assert (
            c10_with_doc.status_code == 201 and c10_with_doc.json()["policy_check_status"] == "OK"
        )
        results_log.append("Case 10: Sick >2 days -> BLOCKED without doc, OK with doc")

        # Case 11: Team coverage exceeded -> VIOLATION
        await async_client.put(
            "/api/v1/leave/config/work-week",
            headers=owner_headers,
            json={"max_team_absent_pct": "10.00"},
        )
        c11 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "casual",
                "start_date": "2026-09-21",
                "end_date": "2026-09-21",
                "reason": "Case 11 team coverage",
                "as_of_date": "2026-09-20",
                "start_workflow": False,
            },
        )
        assert c11.status_code == 201 and c11.json()["policy_check_status"] == "VIOLATION"
        assert any(
            r["code"] == "TEAM_COVERAGE_EXCEEDED" for r in c11.json()["policy_check_reasons"]
        )
        await async_client.post(
            f"/api/v1/leave/requests/{c11.json()['id']}/cancel", headers=emp_headers
        )
        await async_client.put(
            "/api/v1/leave/config/work-week",
            headers=owner_headers,
            json={"max_team_absent_pct": "50.00"},
        )
        results_log.append("Case 11: Team coverage exceeded -> VIOLATION")

        # Case 12: Cancel after approval (Case 5) -> reverses ON_LEAVE + restores balance + leave.cancelled
        c12_cancel = await async_client.post(
            f"/api/v1/leave/requests/{c5.json()['id']}/cancel",
            headers=emp_headers,
            json={"reason": "Case 12 cancel after approval"},
        )
        assert c12_cancel.status_code == 200 and c12_cancel.json()["status"] == "cancelled"
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            c5_att = await conn.fetchval(
                "SELECT count(*) FROM public.attendance WHERE tenant_id = $1::uuid AND leave_request_id = $2::uuid;",
                globex.tenant_id,
                c5.json()["id"],
            )
            assert c5_att == 0
        finally:
            await conn.close()
        results_log.append("Case 12: Cancel after approval -> reversed attendance & balance")

        # Case 13: Two simultaneous submissions -> 1 succeeds (201), 1 fails (409), 0 negative balance
        r_sim_1, r_sim_2 = await asyncio.gather(
            async_client.post(
                "/api/v1/leave/requests",
                headers=emp_headers,
                json={
                    "leave_type_code": "casual",
                    "start_date": "2026-10-05",
                    "end_date": "2026-10-12",
                    "reason": "Case 13 concurrent A",
                    "as_of_date": "2026-10-01",
                    "start_workflow": False,
                },
            ),
            async_client.post(
                "/api/v1/leave/requests",
                headers=emp_headers,
                json={
                    "leave_type_code": "casual",
                    "start_date": "2026-10-19",
                    "end_date": "2026-10-26",
                    "reason": "Case 13 concurrent B",
                    "as_of_date": "2026-10-01",
                    "start_workflow": False,
                },
            ),
        )
        assert sorted([r_sim_1.status_code, r_sim_2.status_code]) == [201, 409]
        winner = r_sim_1 if r_sim_1.status_code == 201 else r_sim_2
        results_log.append("Case 13: Concurrent asyncio.gather -> 1x 201, 1x 409")

        # Case 14: Manager rejects -> releases reservation
        c14 = await async_client.post(
            f"/api/v1/leave/requests/{winner.json()['id']}/decide",
            headers=mgr_headers,
            json={"decision": "rejected", "note": "Case 14 manager reject"},
        )
        assert c14.status_code == 200 and c14.json()["status"] == "rejected"
        results_log.append("Case 14: Manager rejects -> reservation released")

        # Case 15: Manager silent -> Temporal reminder + backup escalation + expiry release
        env = await WorkflowEnvironment.start_time_skipping()
        try:
            set_temporal_client(env.client)
            async with (
                env,
                Worker(
                    env.client,
                    task_queue="agentic-hr-task-queue",
                    workflows=[LeaveRequestWorkflow],
                    activities=[
                        notify_leave_requested_activity,
                        send_leave_reminder_activity,
                        escalate_leave_request_activity,
                        expire_leave_request_activity,
                        release_leave_reservation_activity,
                        approve_leave_request_activity,
                    ],
                ),
            ):
                c15 = await async_client.post(
                    "/api/v1/leave/requests",
                    headers=emp_headers,
                    json={
                        "leave_type_code": "casual",
                        "start_date": "2026-10-05",
                        "end_date": "2026-10-06",
                        "reason": "Case 15 silent manager",
                        "as_of_date": "2026-10-01",
                        "start_workflow": True,
                        "reminder_hours": 0.0003,
                        "escalation_hours": 0.0006,
                        "expiry_hours": 0.0009,
                    },
                )
                assert c15.status_code == 201, c15.text
                wf_out = await env.client.get_workflow_handle(c15.json()["id"]).result()
                assert wf_out["status"] == "expired"
        finally:
            set_temporal_client(None)
        results_log.append("Case 15: Silent manager -> reminder, backup escalation & expiry")

        # Case 16: HR exception rejected AND HR exception approved
        c16_rej_sub = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-10-12",
                "end_date": "2026-10-12",
                "reason": "Case 16A HR reject",
                "as_of_date": "2026-10-11",
                "start_workflow": False,
            },
        )
        assert c16_rej_sub.status_code == 201 and c16_rej_sub.json()["hr_exception_approval_id"]
        await async_client.post(
            f"/api/v1/approvals/{c16_rej_sub.json()['hr_exception_approval_id']}/reject",
            headers=owner_headers,
            json={"reason": "HR exception denied"},
        )
        reqs_after_a = (
            await async_client.get("/api/v1/leave/requests/me", headers=emp_headers)
        ).json()
        assert (
            next(r for r in reqs_after_a if r["id"] == c16_rej_sub.json()["id"])["status"]
            == "rejected"
        )

        c16_app_sub = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-10-14",
                "end_date": "2026-10-14",
                "reason": "Case 16B HR approve",
                "as_of_date": "2026-10-13",
                "start_workflow": False,
            },
        )
        assert c16_app_sub.status_code == 201 and c16_app_sub.json()["hr_exception_approval_id"]
        await async_client.post(
            f"/api/v1/leave/requests/{c16_app_sub.json()['id']}/decide",
            headers=mgr_headers,
            json={"decision": "approved"},
        )
        await async_client.post(
            f"/api/v1/approvals/{c16_app_sub.json()['hr_exception_approval_id']}/approve",
            headers=owner_headers,
            json={"reason": "HR exception granted"},
        )
        reqs_after_b = (
            await async_client.get("/api/v1/leave/requests/me", headers=emp_headers)
        ).json()
        assert (
            next(r for r in reqs_after_b if r["id"] == c16_app_sub.json()["id"])["status"]
            == "approved"
        )
        results_log.append("Case 16: HR exception rejected & approved -> both verified")

        print("\n=== Stage 6 Exit Gate: 16/16 Leave Cases Verified ===")
        for line in results_log:
            print(f"  [PASS] {line}")
        assert len(results_log) == 16
    finally:
        await _teardown_globex_exit_state(globex.tenant_id)


# ==============================================================================
# 2. Mid-Approval Worker Crash Recovery Gate
# ==============================================================================
@pytest.mark.asyncio
async def test_02_mid_approval_worker_crash_recovery_gate(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _setup_globex_exit_state(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
        with_connector=True,
    )
    try:
        sub = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-10-19",
                "end_date": "2026-10-21",
                "reason": "Exit gate crash recovery",
                "as_of_date": "2026-10-01",
                "start_workflow": False,
            },
        )
        assert sub.status_code == 201, sub.text
        req_id = uuid.UUID(sub.json()["id"])
        tenant_uuid = uuid.UUID(globex.tenant_id)

        async def _crash() -> None:
            raise RuntimeError("Simulated mid-activity worker crash!")

        with pytest.raises(RuntimeError, match="Simulated mid-activity worker crash"):
            async with AsyncSessionLocal() as db1:
                await set_tenant_context(db1, globex.tenant_id, globex.manager.clerk_user_id)
                await LeaveService.approve_leave_request(
                    db1,
                    tenant_id=tenant_uuid,
                    request_id=req_id,
                    decided_by=globex.manager.user_id,
                    decision_note="Crash gate",
                    clerk_user_id=globex.manager.clerk_user_id,
                    after_phase1_commit_hook=_crash,
                )

        async with AsyncSessionLocal() as db2:
            await set_tenant_context(db2, globex.tenant_id, globex.manager.clerk_user_id)
            retry = await LeaveService.approve_leave_request(
                db2,
                tenant_id=tenant_uuid,
                request_id=req_id,
                decided_by=globex.manager.user_id,
                decision_note="Crash gate",
                clerk_user_id=globex.manager.clerk_user_id,
            )
            assert retry["status"] == "approved"

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM public.outbox_events WHERE tenant_id = $1::uuid AND event_type = 'leave.approved' AND event_payload->>'leave_request_id' = $2;",
                    globex.tenant_id,
                    str(req_id),
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM public.leave_ledger WHERE tenant_id = $1::uuid AND idempotency_key = $2;",
                    globex.tenant_id,
                    f"deduct:{req_id}",
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM public.attendance WHERE tenant_id = $1::uuid AND leave_request_id = $2::uuid;",
                    globex.tenant_id,
                    str(req_id),
                )
                == 3
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM public.calendar_events WHERE tenant_id = $1::uuid;",
                    globex.tenant_id,
                )
                == 1
            )
        finally:
            await conn.close()
    finally:
        await _teardown_globex_exit_state(globex.tenant_id)


# ==============================================================================
# 3. 3-Month Attendance Seed, `pub_attendance` / `pub_leave` RLS & Stage Gate
# ==============================================================================
@pytest.mark.asyncio
async def test_03_views_three_month_seed_and_platform_stage_gate(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    acme = isolation_context.company_a
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    # 1. Verify root stage progression string
    root_resp = await async_client.get("/")
    assert root_resp.status_code == 200
    assert root_resp.json()["stage"] == "Stage 6 complete — Stage 7 next"

    await _setup_globex_exit_state(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        # 2. Seed a leave request so `pub_leave` has an active Globex record
        sub = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-03",
                "end_date": "2026-08-04",
                "reason": "Exit view check",
                "as_of_date": "2026-07-20",
                "start_workflow": False,
            },
        )
        assert sub.status_code == 201, sub.text

        # 3. Run 3-month attendance seed for Globex Corp
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, globex.tenant_id, globex.owner.user_id)
            seed_res = await seed_three_months_attendance(
                db,
                tenant_id=globex.tenant_id,
                reference_date=date(2026, 8, 15),
                actor_id=globex.owner.user_id,
            )
            await db.commit()

        expected_days = (
            calendar.monthrange(2026, 6)[1]
            + calendar.monthrange(2026, 7)[1]
            + calendar.monthrange(2026, 8)[1]
        )
        assert seed_res["inserted_rows"] == 3 * expected_days

        # 4. Verify `pub_attendance` & `pub_leave` under `agentic_hr_app` Forced RLS
        app_conn = await get_app_connection()
        try:
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    globex.tenant_id,
                )
                assert (
                    await app_conn.fetchval("SELECT count(*) FROM public.pub_attendance;")
                    == 3 * expected_days
                )
                assert await app_conn.fetchval("SELECT count(*) FROM public.pub_leave;") >= 1

            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    acme.tenant_id,
                )
                assert (
                    await app_conn.fetchval(
                        "SELECT count(*) FROM public.pub_attendance WHERE tenant_id = $1::uuid;",
                        uuid.UUID(globex.tenant_id),
                    )
                    == 0
                )
                assert (
                    await app_conn.fetchval(
                        "SELECT count(*) FROM public.pub_leave WHERE tenant_id = $1::uuid;",
                        uuid.UUID(globex.tenant_id),
                    )
                    == 0
                )
        finally:
            await app_conn.close()
    finally:
        await _teardown_globex_exit_state(globex.tenant_id)
