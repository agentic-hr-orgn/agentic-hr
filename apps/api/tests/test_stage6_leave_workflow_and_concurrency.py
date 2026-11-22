"""
Stage 6 Prompt 3 Verification Suite:
1. Bilingual Leave Intake Agent (`POST /api/v1/leave/intake`) — clarifying question -> confirm_card.
2. Simultaneous double-submission race (`asyncio.gather`) serialized via `SELECT ... FOR UPDATE`.
3. `VIOLATION` path creating Stage 2 `leave.exception` (HR reject + HR approve/manager approve).
4. Strict approver / escalated backup approver RBAC (unrelated manager -> 403), manager reject,
   and cancel-after-approval reversing ONLY linked attendance rows & blocking locked months (409).
5. Sandboxed Temporal `LeaveRequestWorkflow` short-timer escalation (reminder -> backup -> expiry).
6. Mid-approval crash & retry proving single idempotent `approve_leave_request_activity`
   (exactly 1 `leave.approved` outbox event, 1 `deduct:{id}` ledger row, 1 calendar event).
"""

import asyncio
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
from app.core.temporal import reset_temporal_client, set_temporal_client
from app.db.session import AsyncSessionLocal
from app.models.tenant import set_tenant_context
from app.services.leave_service import LeaveService
from app.workflows import (
    LeaveRequestWorkflow,
    approve_leave_request_activity,
    escalate_leave_request_activity,
    expire_leave_request_activity,
    notify_leave_requested_activity,
    release_leave_reservation_activity,
    send_leave_reminder_activity,
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
        reset_temporal_client()
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


async def _reset_globex_leave_state(
    *,
    tenant_id: str,
    employee_user_id: str,
    manager_user_id: str,
    owner_user_id: str,
    hire_date: date = date(2024, 1, 1),
    with_connector: bool = False,
) -> dict[str, str]:
    """Sets up deterministic Globex Corp employees (manager -> employee) and Stage 6 defaults."""
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
            "approvals",
            "connector_credentials",
            "employees",
            "departments",
        ):
            await conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                tenant_id,
            )

        dept_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.departments (tenant_id, name, code)
                VALUES ($1::uuid, 'Product Engineering', 'ENG')
                RETURNING id;
                """,
                tenant_id,
            )
        )
        mgr_emp_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.employees (
                    tenant_id, user_id, employee_code, full_name, email,
                    department_id, job_title, employment_type, join_date, status
                )
                VALUES (
                    $1::uuid, $2::uuid, 'GLX-MGR-1', 'Globex Manager',
                    'mgr.s6@globex.internal', $3::uuid,
                    'Engineering Manager', 'full_time', '2023-01-01', 'active'
                )
                RETURNING id;
                """,
                tenant_id,
                manager_user_id,
                dept_id,
            )
        )
        emp_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.employees (
                    tenant_id, user_id, employee_code, full_name, email,
                    department_id, manager_id, job_title, employment_type, join_date, status
                )
                VALUES (
                    $1::uuid, $2::uuid, 'GLX-EMP-1', 'Globex Employee',
                    'emp.s6@globex.internal', $3::uuid, $4::uuid,
                    'Software Engineer', 'full_time', $5, 'active'
                )
                RETURNING id;
                """,
                tenant_id,
                employee_user_id,
                dept_id,
                mgr_emp_id,
                hire_date,
            )
        )
        await conn.execute(
            """
            INSERT INTO public.work_week (
                tenant_id, weekend_days, probation_months, max_team_absent_pct,
                min_team_present, backup_approver_user_id
            )
            VALUES ($1::uuid, '["Saturday", "Sunday"]'::jsonb, 3, 60.00, 1, $2::uuid);
            """,
            tenant_id,
            owner_user_id,
        )

        if with_connector:
            cred_id = uuid.uuid4()
            enc_acc = encrypt_field(
                "fake-acc-s6",
                EncryptionContext(tenant_id, "connector_credential", cred_id, "access_token"),
            )
            enc_ref = encrypt_field(
                "fake-ref-s6",
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
                    $1::uuid, $2::uuid, 'google', 'owner@globex.internal',
                    '["https://www.googleapis.com/auth/calendar.events","https://www.googleapis.com/auth/gmail.send"]'::jsonb,
                    $3, $4, NOW() + INTERVAL '2 hours', 'connected', false
                );
                """,
                str(cred_id),
                tenant_id,
                enc_acc,
                enc_ref,
            )

        return {"dept_id": dept_id, "mgr_emp_id": mgr_emp_id, "emp_id": emp_id}
    finally:
        await conn.close()


async def _teardown_globex_leave(tenant_id: str) -> None:
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
            "approvals",
            "connector_credentials",
            "employees",
            "departments",
        ):
            await conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                tenant_id,
            )
    finally:
        await conn.close()


# ==============================================================================
# 1. Bilingual Leave Intake Agent (Clarifying Question -> Confirm Card)
# ==============================================================================
@pytest.mark.asyncio
async def test_01_bilingual_leave_intake_clarify_then_confirm_card(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _reset_globex_leave_state(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        # Turn 1: Ambiguous Roman Urdu "chutti" with dates -> needs_clarification (1 question)
        r1 = await async_client.post(
            "/api/v1/leave/intake",
            headers=emp_headers,
            json={
                "message": "Mujhe 2026-07-13 se 2026-07-15 tak chutti chahiye",
                "conversation_context": {"as_of_date": "2026-07-01"},
            },
        )
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["status"] == "needs_clarification"
        assert d1["clarifying_question"] is not None
        assert "annual" in d1["clarifying_question"].lower()
        assert d1["confirm_card"] is None

        # Turn 2: Provide leave type + reason -> ready_to_confirm with confirm_card
        r2 = await async_client.post(
            "/api/v1/leave/intake",
            headers=emp_headers,
            json={
                "message": "Annual leave because family wedding",
                "conversation_context": d1["extracted"],
            },
        )
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2["status"] == "ready_to_confirm"
        card = d2["confirm_card"]
        assert card["leave_type_code"] == "annual"
        assert card["start_date"] == "2026-07-13"
        assert card["end_date"] == "2026-07-15"
        assert Decimal(str(card["working_days"])) == Decimal("3.00")
        assert Decimal(str(card["balance_before"])) == Decimal("20.00")
        assert Decimal(str(card["balance_after"])) == Decimal("17.00")
        assert card["policy_check_status"] == "OK"
    finally:
        await _teardown_globex_leave(globex.tenant_id)


# ==============================================================================
# 2. Simultaneous Double-Submission Concurrency Race (SELECT ... FOR UPDATE)
# ==============================================================================
@pytest.mark.asyncio
async def test_02_concurrent_double_submission_prevents_balance_overspend(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    ids = await _reset_globex_leave_state(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        # Seed balances first & trim annual allocation to 5.00 days
        bal_resp = await async_client.get(
            "/api/v1/leave/balances/me",
            headers=emp_headers,
            params={"year": 2026},
        )
        assert bal_resp.status_code == 200

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                """
                UPDATE public.leave_balances lb
                SET allocated_days = 5.00
                FROM public.leave_types lt
                WHERE lb.leave_type_id = lt.id
                  AND lb.tenant_id = $1::uuid
                  AND lb.employee_id = $2::uuid
                  AND lt.code = 'annual'
                  AND lb.year = 2026;
                """,
                globex.tenant_id,
                ids["emp_id"],
            )
        finally:
            await conn.close()

        # Submit two 4-day requests simultaneously on non-overlapping weeks (total 8 > 5 available)
        p1 = {
            "leave_type_code": "annual",
            "start_date": "2026-07-06",
            "end_date": "2026-07-09",  # 4 working days
            "reason": "Concurrent trip 1",
            "as_of_date": "2026-06-01",
            "start_workflow": False,
        }
        p2 = {
            "leave_type_code": "annual",
            "start_date": "2026-07-20",
            "end_date": "2026-07-23",  # 4 working days
            "reason": "Concurrent trip 2",
            "as_of_date": "2026-06-01",
            "start_workflow": False,
        }
        resp1, resp2 = await asyncio.gather(
            async_client.post("/api/v1/leave/requests", headers=emp_headers, json=p1),
            async_client.post("/api/v1/leave/requests", headers=emp_headers, json=p2),
        )
        statuses = sorted([resp1.status_code, resp2.status_code])
        assert statuses == [201, 409], (
            f"Expected [201, 409], got {statuses}: {resp1.text} | {resp2.text}"
        )

        bal_after = await async_client.get(
            "/api/v1/leave/balances/me",
            headers=emp_headers,
            params={"year": 2026},
        )
        annual = next(b for b in bal_after.json() if b["leave_type_code"] == "annual")
        assert Decimal(str(annual["reserved_days"])) == Decimal("4.00")
        assert Decimal(str(annual["available_days"])) == Decimal("1.00")
    finally:
        await _teardown_globex_leave(globex.tenant_id)


# ==============================================================================
# 3. VIOLATION Path -> Stage 2 `leave.exception` (HR Reject & HR Approve)
# ==============================================================================
@pytest.mark.asyncio
async def test_03_violation_hr_exception_reject_and_approve_paths(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    # Hire date 2026-05-01 puts employee in probation for July 2026 -> VIOLATION
    ids = await _reset_globex_leave_state(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
        hire_date=date(2026, 5, 1),
    )
    try:
        # Case A: HR rejects leave.exception -> releases reservation & marks leave rejected
        sub_a = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-07-06",
                "end_date": "2026-07-07",
                "reason": "Probation exception test A",
                "as_of_date": "2026-07-01",
                "start_workflow": False,
            },
        )
        assert sub_a.status_code == 201, sub_a.text
        req_a = sub_a.json()
        assert req_a["policy_check_status"] == "VIOLATION"
        assert req_a["hr_exception_approval_id"] is not None

        rej_hr = await async_client.post(
            f"/api/v1/approvals/{req_a['hr_exception_approval_id']}/reject",
            headers=owner_headers,
            json={"reason": "Probation policy strict this quarter"},
        )
        assert rej_hr.status_code == 200, rej_hr.text

        my_reqs = (await async_client.get("/api/v1/leave/requests/me", headers=emp_headers)).json()
        assert my_reqs[0]["status"] == "rejected"

        bal_mid = (
            await async_client.get(
                "/api/v1/leave/balances/me", headers=emp_headers, params={"year": 2026}
            )
        ).json()
        annual_mid = next(b for b in bal_mid if b["leave_type_code"] == "annual")
        assert Decimal(str(annual_mid["reserved_days"])) == Decimal("0.00")

        # Case B: Manager approves first -> status becomes pending_hr_exception -> HR approves -> approved!
        # Range spans Fri Aug 14 (Independence Day) + Sat Aug 15 + Sun Aug 16 + Mon Aug 17 => 2 working days (Aug 13, Aug 17)
        sub_b = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-07-23",
                "end_date": "2026-07-24",
                "reason": "Probation exception test B",
                "as_of_date": "2026-07-01",
                "start_workflow": False,
            },
        )
        assert sub_b.status_code == 201, sub_b.text
        req_b = sub_b.json()

        # Manager approves while HR exception still pending -> status becomes pending_hr_exception
        mgr_dec = await async_client.post(
            f"/api/v1/leave/requests/{req_b['id']}/decide",
            headers=mgr_headers,
            json={"decision": "approved", "note": "Manager ok"},
        )
        assert mgr_dec.status_code == 200, mgr_dec.text
        assert mgr_dec.json()["status"] == "pending_hr_exception"

        # Now HR Head (Owner) approves the Stage 2 `leave.exception` -> automatically finalizes approval
        app_hr = await async_client.post(
            f"/api/v1/approvals/{req_b['hr_exception_approval_id']}/approve",
            headers=owner_headers,
            json={"reason": "Approved by HR exception"},
        )
        assert app_hr.status_code == 200, app_hr.text

        final_reqs = (
            await async_client.get("/api/v1/leave/requests/me", headers=emp_headers)
        ).json()
        b_final = next(r for r in final_reqs if r["id"] == req_b["id"])
        assert b_final["status"] == "approved"

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            att_dates = await conn.fetch(
                """
                SELECT attendance_date, status, source
                FROM public.attendance
                WHERE tenant_id = $1::uuid AND employee_id = $2::uuid
                ORDER BY attendance_date;
                """,
                globex.tenant_id,
                ids["emp_id"],
            )
            assert [r["attendance_date"] for r in att_dates] == [
                date(2026, 7, 23),
                date(2026, 7, 24),
            ]
            assert all(
                r["status"] == "ON_LEAVE" and r["source"] == "leave_approval" for r in att_dates
            )
        finally:
            await conn.close()
    finally:
        await _teardown_globex_leave(globex.tenant_id)


# ==============================================================================
# 4. Strict RBAC, Reject, Working-Date ON_LEAVE & Cancel-After-Approval
# ==============================================================================
@pytest.mark.asyncio
async def test_04_rbac_reject_and_cancel_after_approval_reversal(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    ids = await _reset_globex_leave_state(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )
    try:
        # 1. Submit leave spanning Independence Day + weekend (Aug 13..17 => 2 working days: Aug 13, Aug 17)
        sub = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "annual",
                "start_date": "2026-08-13",
                "end_date": "2026-08-17",
                "reason": "August long weekend",
                "as_of_date": "2026-08-01",
                "start_workflow": False,
            },
        )
        assert sub.status_code == 201, sub.text
        req_id = sub.json()["id"]
        assert Decimal(str(sub.json()["working_days"])) == Decimal("2.00")

        # 2. Unrelated manager cannot decide (create a second manager user without hr_head)
        # Demote globex.manager is_hr_head=false and assign backup approver/manager check
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
            # Temporarily reassign approver to owner to verify globex.manager gets 403
            await conn.execute(
                "UPDATE public.leave_requests SET approver_user_id = $1::uuid WHERE id = $2::uuid;",
                globex.owner.user_id,
                req_id,
            )
        finally:
            await conn.close()

        forb_dec = await async_client.post(
            f"/api/v1/leave/requests/{req_id}/decide",
            headers=mgr_headers,
            json={"decision": "approved"},
        )
        assert forb_dec.status_code == 403

        # Restore manager as approver and approve
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.leave_requests SET approver_user_id = $1::uuid WHERE id = $2::uuid;",
                globex.manager.user_id,
                req_id,
            )
        finally:
            await conn.close()

        inbox = await async_client.get("/api/v1/leave/inbox", headers=mgr_headers)
        assert inbox.status_code == 200
        assert any(item["id"] == req_id for item in inbox.json())

        app_resp = await async_client.post(
            f"/api/v1/leave/requests/{req_id}/decide",
            headers=mgr_headers,
            json={"decision": "approved", "note": "Enjoy"},
        )
        assert app_resp.status_code == 200, app_resp.text
        assert app_resp.json()["status"] == "approved"

        # Verify ON_LEAVE rows exist ONLY on Aug 13 and Aug 17 (NOT Aug 14 holiday or Aug 15/16 weekend)
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            att_rows = await conn.fetch(
                """
                SELECT attendance_date FROM public.attendance
                WHERE tenant_id = $1::uuid AND leave_request_id = $2::uuid
                ORDER BY attendance_date;
                """,
                globex.tenant_id,
                req_id,
            )
            assert [r["attendance_date"] for r in att_rows] == [
                date(2026, 8, 13),
                date(2026, 8, 17),
            ]
            # Insert an unrelated PRESENT attendance row on Aug 12 to prove cancel reverses ONLY leave_request_id == req_id
            await conn.execute(
                """
                INSERT INTO public.attendance (tenant_id, employee_id, attendance_date, status, source)
                VALUES ($1::uuid, $2::uuid, $3, 'PRESENT', 'check_in');
                """,
                globex.tenant_id,
                ids["emp_id"],
                date(2026, 8, 12),
            )
        finally:
            await conn.close()

        # 3. Cancel after approval -> reverses balance, deletes ONLY linked attendance, emits leave.cancelled
        cancel_resp = await async_client.post(
            f"/api/v1/leave/requests/{req_id}/cancel",
            headers=emp_headers,
            json={"reason": "Plans changed"},
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        assert cancel_resp.json()["status"] == "cancelled"

        bal_after = (
            await async_client.get(
                "/api/v1/leave/balances/me", headers=emp_headers, params={"year": 2026}
            )
        ).json()
        annual = next(b for b in bal_after if b["leave_type_code"] == "annual")
        assert Decimal(str(annual["used_days"])) == Decimal("0.00")
        assert Decimal(str(annual["available_days"])) == Decimal("20.00")

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            remaining_att = await conn.fetch(
                "SELECT attendance_date FROM public.attendance WHERE tenant_id = $1::uuid AND employee_id = $2::uuid;",
                globex.tenant_id,
                ids["emp_id"],
            )
            assert [r["attendance_date"] for r in remaining_att] == [date(2026, 8, 12)]

            cancelled_outbox = await conn.fetchval(
                """
                SELECT count(*) FROM public.outbox_events
                WHERE tenant_id = $1::uuid AND event_type = 'leave.cancelled'
                  AND event_payload->>'leave_request_id' = $2;
                """,
                globex.tenant_id,
                req_id,
            )
            assert cancelled_outbox == 1
        finally:
            await conn.close()

        # 4. Month-lock guard blocks cancel-after-approval with 409
        sub2 = await async_client.post(
            "/api/v1/leave/requests",
            headers=emp_headers,
            json={
                "leave_type_code": "casual",
                "start_date": "2026-09-07",
                "end_date": "2026-09-07",
                "reason": "Month lock cancel guard test",
                "as_of_date": "2026-09-01",
                "start_workflow": False,
            },
        )
        req2_id = sub2.json()["id"]
        await async_client.post(
            f"/api/v1/leave/requests/{req2_id}/decide",
            headers=owner_headers,
            json={"decision": "approved"},
        )
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "INSERT INTO public.attendance_month_locks (tenant_id, year_month) VALUES ($1::uuid, '2026-09');",
                globex.tenant_id,
            )
        finally:
            await conn.close()

        locked_cancel = await async_client.post(
            f"/api/v1/leave/requests/{req2_id}/cancel",
            headers=emp_headers,
            json={"reason": "Should be blocked"},
        )
        assert locked_cancel.status_code == 409
        assert "ATTENDANCE_MONTH_LOCKED" in locked_cancel.text
    finally:
        await _teardown_globex_leave(globex.tenant_id)


# ==============================================================================
# 5. Durable Temporal `LeaveRequestWorkflow` (Short-Timer Escalation & Expiry)
# ==============================================================================
@pytest.mark.asyncio
async def test_05_temporal_workflow_silent_manager_escalation_and_expiry(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _reset_globex_leave_state(
        tenant_id=globex.tenant_id,
        employee_user_id=globex.employee.user_id,
        manager_user_id=globex.manager.user_id,
        owner_user_id=globex.owner.user_id,
    )

    env = await WorkflowEnvironment.start_time_skipping()
    set_temporal_client(env.client)
    task_queue = "agentic-hr-task-queue"

    try:
        async with (
            env,
            Worker(
                env.client,
                task_queue=task_queue,
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
            sub = await async_client.post(
                "/api/v1/leave/requests",
                headers=emp_headers,
                json={
                    "leave_type_code": "annual",
                    "start_date": "2026-10-05",
                    "end_date": "2026-10-06",
                    "reason": "Silent manager escalation test",
                    "as_of_date": "2026-09-20",
                    "start_workflow": True,
                    "reminder_hours": 0.0003,
                    "escalation_hours": 0.0006,
                    "expiry_hours": 0.0009,
                },
            )
            assert sub.status_code == 201, sub.text
            req_id = sub.json()["id"]

            handle = env.client.get_workflow_handle(req_id)
            wf_result = await handle.result()
            assert wf_result["status"] == "expired"

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            row = await conn.fetchrow(
                "SELECT status, escalated_to_user_id FROM public.leave_requests WHERE id = $1::uuid;",
                req_id,
            )
            assert row["status"] == "expired"
            assert str(row["escalated_to_user_id"]) == globex.owner.user_id

            reminder_audit = await conn.fetchval(
                "SELECT count(*) FROM public.audit_logs WHERE tenant_id = $1::uuid AND target_id = $2 AND action = 'leave.reminder_sent';",
                globex.tenant_id,
                req_id,
            )
            assert reminder_audit == 1
        finally:
            await conn.close()
    finally:
        await _teardown_globex_leave(globex.tenant_id)


# ==============================================================================
# 6. Mid-Approval Crash & Retry Idempotency (Exactly 1 Outbox / Ledger / Cal)
# ==============================================================================
@pytest.mark.asyncio
async def test_06_mid_approval_crash_and_retry_is_100_percent_idempotent(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _reset_globex_leave_state(
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
                "start_date": "2026-10-12",
                "end_date": "2026-10-14",
                "reason": "Crash recovery test",
                "as_of_date": "2026-10-01",
                "start_workflow": False,
            },
        )
        assert sub.status_code == 201, sub.text
        req_id = uuid.UUID(sub.json()["id"])
        tenant_uuid = uuid.UUID(globex.tenant_id)

        async def _simulated_worker_crash() -> None:
            raise RuntimeError("Simulated mid-approval worker crash after Phase 1 commit!")

        # Attempt 1: Crashes right after Phase 1 DB commit (before Calendar event)
        with pytest.raises(RuntimeError, match="Simulated mid-approval worker crash"):
            async with AsyncSessionLocal() as db1:
                await set_tenant_context(db1, globex.tenant_id, globex.manager.clerk_user_id)
                await LeaveService.approve_leave_request(
                    db1,
                    tenant_id=tenant_uuid,
                    request_id=req_id,
                    decided_by=globex.manager.user_id,
                    decision_note="Crash test",
                    clerk_user_id=globex.manager.clerk_user_id,
                    after_phase1_commit_hook=_simulated_worker_crash,
                )

        # Attempt 2 (Retry): Must complete cleanly with ZERO duplicate rows
        async with AsyncSessionLocal() as db2:
            await set_tenant_context(db2, globex.tenant_id, globex.manager.clerk_user_id)
            retry_res = await LeaveService.approve_leave_request(
                db2,
                tenant_id=tenant_uuid,
                request_id=req_id,
                decided_by=globex.manager.user_id,
                decision_note="Crash test",
                clerk_user_id=globex.manager.clerk_user_id,
            )
            assert retry_res["status"] == "approved"

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            outbox_cnt = await conn.fetchval(
                """
                SELECT count(*) FROM public.outbox_events
                WHERE tenant_id = $1::uuid AND event_type = 'leave.approved'
                  AND event_payload->>'leave_request_id' = $2;
                """,
                globex.tenant_id,
                str(req_id),
            )
            assert outbox_cnt == 1

            deduct_cnt = await conn.fetchval(
                """
                SELECT count(*) FROM public.leave_ledger
                WHERE tenant_id = $1::uuid AND idempotency_key = $2;
                """,
                globex.tenant_id,
                f"deduct:{req_id}",
            )
            assert deduct_cnt == 1

            att_cnt = await conn.fetchval(
                "SELECT count(*) FROM public.attendance WHERE tenant_id = $1::uuid AND leave_request_id = $2::uuid;",
                globex.tenant_id,
                str(req_id),
            )
            assert att_cnt == 3

            cal_cnt = await conn.fetchval(
                "SELECT count(*) FROM public.calendar_events WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
            assert cal_cnt == 1
        finally:
            await conn.close()
    finally:
        await _teardown_globex_leave(globex.tenant_id)
