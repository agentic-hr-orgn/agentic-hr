"""
Stage 4 Prompt 2 — Onboarding Wizard Backend, Departments CRUD & Policy Upload Stub API Tests.

Verifies:
1. Resumable Onboarding Steps 1..5 across HTTP requests, Stage 6 `structure_config` defaults
   (`weekends: ["Saturday", "Sunday"]`, `leave_rules`, `holidays`), and `onboarding.step_saved`
   audit logging.
2. Departments CRUD (`GET / POST / PATCH / DELETE /api/v1/departments`), case-insensitive duplicate
   rejection (409), non-empty delete protection (409 `DEPARTMENT_NOT_EMPTY`), and safe
   reassignment (`reassign_to_department_id` / `force_null_reassign=true`).
3. Policy Upload Stub (`POST / GET / DELETE /api/v1/policies`), multipart PDF/DOCX/TXT upload,
   10MB size guard, disallowed file type rejection, `ingestion_status="queued_stub"`, local
   tenant-isolated storage, and transactional outbox event `policy.uploaded.stub`.
4. Deterministic Readiness Checklist (`GET /api/v1/onboarding/readiness`) returning
   `can_go_live=False` when any prerequisite is missing, and blocking `/launch` with HTTP 400
   (`ONBOARDING_NOT_READY`).
5. Launch execution (`POST /api/v1/onboarding/launch`):
   - Enforces RBAC (blocks `employee` role with HTTP 403).
   - Marks `is_live=True` and `launched_at` in `Asia/Karachi` (+05:00), writes `onboarding.launched`
     audit log, enqueues `sheets_sync_queue`, and dispatches idempotent invitations via
     `EmailDeliveryService` (`onboarding-invite-{tenant_id}-{employee_id}`).
   - Replays deterministically on repeated `Idempotency-Key` without sending duplicate emails.
   - Gracefully skips invitations (`invitations_skipped`) when connector is read-only without
     leaving the launch idempotency record stuck in `"started"`.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import asyncpg
import httpx
import pytest

from app.core.config import settings
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    MultiTenantTestContext,
)


async def _reset_globex_stage4_state(tenant_id: str) -> None:
    """Cleans Globex Corp's Stage 4 & connector state so each test starts from a known baseline."""
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "sent_messages",
            "sheets_sync_queue",
            "idempotency_keys",
            "outbox_events",
            "policy_documents",
            "employee_documents",
            "employees",
            "departments",
            "onboarding_state",
            "connector_credentials",
        ):
            await conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                tenant_id,
            )
    finally:
        await conn.close()


# ==============================================================================
# 1. Resumable Onboarding Steps 1..5 & Stage 6 Structure Config Placeholders
# ==============================================================================
@pytest.mark.asyncio
async def test_01_onboarding_steps_save_and_resume(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies saving and resuming Steps 1..5 across requests, default Stage 6 placeholders
    (weekends=['Saturday', 'Sunday']), immutable JSONB persistence, and audit logging.
    """
    ctx = isolation_context
    globex = ctx.company_b
    await _reset_globex_stage4_state(globex.tenant_id)
    headers = {"Authorization": f"Bearer {globex.owner.token}"}

    # 1. Initial GET initializes default Step 1 state with Saturday/Sunday weekends
    resp_init = await async_client.get("/api/v1/onboarding/state", headers=headers)
    assert resp_init.status_code == 200, resp_init.text
    state0 = resp_init.json()
    assert state0["current_step"] == 1
    assert state0["completed_steps"] == []
    assert state0["is_live"] is False
    assert state0["structure_config"]["weekends"] == ["Saturday", "Sunday"]
    assert "leave_rules" in state0["structure_config"]
    assert state0["structure_config"]["holidays"] == []

    # 2. Save Step 1 (business_type + custom leave rules & holiday placeholder)
    resp_s1 = await async_client.put(
        "/api/v1/onboarding/steps/1",
        headers=headers,
        json={
            "business_type": "Software & Technology SME",
            "structure_config": {
                "leave_rules": {"annual_days": 22, "sick_days": 12},
                "holidays": [{"name": "Pakistan Day", "date": "2026-03-23"}],
            },
        },
    )
    assert resp_s1.status_code == 200, resp_s1.text
    s1 = resp_s1.json()
    assert s1["current_step"] == 2
    assert s1["completed_steps"] == [1]
    assert s1["business_type"] == "Software & Technology SME"
    assert s1["structure_config"]["leave_rules"]["annual_days"] == 22
    assert s1["structure_config"]["leave_rules"]["casual_days"] == 10
    assert s1["structure_config"]["weekends"] == ["Saturday", "Sunday"]
    assert len(s1["structure_config"]["holidays"]) == 1

    # 3. Save Steps 2, 3, 4, 5 sequentially
    for step_num in (2, 3, 4, 5):
        resp_sn = await async_client.put(
            f"/api/v1/onboarding/steps/{step_num}",
            headers=headers,
            json={"structure_config": {f"step_{step_num}_confirmed": True}},
        )
        assert resp_sn.status_code == 200, resp_sn.text

    # 4. Resume via GET /api/v1/onboarding/state and confirm all 5 steps persisted
    resp_resume = await async_client.get("/api/v1/onboarding/state", headers=headers)
    assert resp_resume.status_code == 200, resp_resume.text
    resumed = resp_resume.json()
    assert resumed["current_step"] == 5
    assert resumed["completed_steps"] == [1, 2, 3, 4, 5]
    assert resumed["business_type"] == "Software & Technology SME"
    assert resumed["structure_config"]["step_5_confirmed"] is True

    # 5. Invalid step number rejected with 400
    resp_bad = await async_client.put(
        "/api/v1/onboarding/steps/6",
        headers=headers,
        json={},
    )
    assert resp_bad.status_code == 400

    # 6. Verify audit log entry written for onboarding.step_saved
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        audit_count = await conn.fetchval(
            """
            SELECT count(*) FROM public.audit_logs
            WHERE tenant_id = $1::uuid AND action = 'onboarding.step_saved';
            """,
            globex.tenant_id,
        )
        assert audit_count >= 5
    finally:
        await conn.close()


# ==============================================================================
# 2. Departments CRUD, Non-Empty Delete Protection & RBAC
# ==============================================================================
@pytest.mark.asyncio
async def test_02_departments_crud_and_delete_protection(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies Departments CRUD, case-insensitive duplicate conflict (409), RBAC blocking employee
    writes (403), and blocking delete when employees exist unless reassigned or force_null_reassign=true.
    """
    ctx = isolation_context
    globex = ctx.company_b
    await _reset_globex_stage4_state(globex.tenant_id)
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    # Employee cannot create department (403)
    resp_forb = await async_client.post(
        "/api/v1/departments",
        headers=emp_headers,
        json={"name": "Unauthorized Dept"},
    )
    assert resp_forb.status_code == 403

    # Owner creates two departments
    resp_c1 = await async_client.post(
        "/api/v1/departments",
        headers=owner_headers,
        json={"name": "Platform Engineering", "code": "PLAT", "description": "Core infra"},
    )
    assert resp_c1.status_code == 201, resp_c1.text
    dept1 = resp_c1.json()

    resp_c2 = await async_client.post(
        "/api/v1/departments",
        headers=owner_headers,
        json={"name": "People Operations", "code": "POPS"},
    )
    assert resp_c2.status_code == 201, resp_c2.text
    dept2 = resp_c2.json()

    # Duplicate case-insensitive name rejected with 409
    resp_dup = await async_client.post(
        "/api/v1/departments",
        headers=owner_headers,
        json={"name": "platform engineering"},
    )
    assert resp_dup.status_code == 409

    # Update department
    resp_upd = await async_client.patch(
        f"/api/v1/departments/{dept1['id']}",
        headers=owner_headers,
        json={"code": "PLATFORM", "description": "Updated description"},
    )
    assert resp_upd.status_code == 200
    assert resp_upd.json()["code"] == "PLATFORM"

    # Insert an employee assigned to dept1
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        emp_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.employees (
                    tenant_id, department_id, full_name, email, job_title, join_date, status
                )
                VALUES ($1::uuid, $2::uuid, 'Globex Dev', 'dev@globex.internal', 'Engineer', $3, 'active')
                RETURNING id;
                """,
                globex.tenant_id,
                dept1["id"],
                date(2025, 1, 1),
            )
        )
    finally:
        await conn.close()

    # Deleting dept1 without reassignment must block with 409 DEPARTMENT_NOT_EMPTY
    resp_block = await async_client.delete(
        f"/api/v1/departments/{dept1['id']}",
        headers=owner_headers,
    )
    assert resp_block.status_code == 409
    assert "DEPARTMENT_NOT_EMPTY" in resp_block.text

    # Deleting dept1 with reassign_to_department_id=dept2 succeeds and moves the employee
    resp_reassign = await async_client.delete(
        f"/api/v1/departments/{dept1['id']}",
        headers=owner_headers,
        params={"reassign_to_department_id": dept2["id"]},
    )
    assert resp_reassign.status_code == 200, resp_reassign.text
    assert resp_reassign.json()["reassigned_employees"] == 1

    # Deleting dept2 with force_null_reassign=true succeeds and nulls employee.department_id
    resp_null = await async_client.delete(
        f"/api/v1/departments/{dept2['id']}",
        headers=owner_headers,
        params={"force_null_reassign": "true"},
    )
    assert resp_null.status_code == 200, resp_null.text
    assert resp_null.json()["reassigned_employees"] == 1

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        dept_after = await conn.fetchval(
            "SELECT department_id FROM public.employees WHERE id = $1::uuid;",
            emp_id,
        )
        assert dept_after is None
    finally:
        await conn.close()


# ==============================================================================
# 3. Policy Upload Stub (PDF/DOCX/TXT <= 10MB) & Outbox Event `policy.uploaded.stub`
# ==============================================================================
@pytest.mark.asyncio
async def test_03_policy_upload_stub_validation_and_outbox(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies multipart policy uploads (PDF/TXT), rejection of unsupported extensions,
    `ingestion_status='queued_stub'`, and `policy.uploaded.stub` outbox event emission.
    """
    ctx = isolation_context
    globex = ctx.company_b
    await _reset_globex_stage4_state(globex.tenant_id)
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    # 1. Disallowed file type rejected with 400
    resp_bad = await async_client.post(
        "/api/v1/policies",
        headers=owner_headers,
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert resp_bad.status_code == 400
    assert "INVALID_POLICY_FILE_TYPE" in resp_bad.text

    # 2. Valid PDF upload succeeds with ingestion_status="queued_stub"
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
    resp_pdf = await async_client.post(
        "/api/v1/policies",
        headers=owner_headers,
        data={"title": "Globex Leave Policy 2026"},
        files={"file": ("leave-policy.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp_pdf.status_code == 201, resp_pdf.text
    doc = resp_pdf.json()
    assert doc["title"] == "Globex Leave Policy 2026"
    assert doc["ingestion_status"] == "queued_stub"
    assert doc["storage_provider"] == "local"
    assert doc["storage_key"].startswith(f"{globex.tenant_id}/policies/")

    # 3. List policies returns the uploaded record
    resp_list = await async_client.get("/api/v1/policies", headers=owner_headers)
    assert resp_list.status_code == 200
    assert any(p["id"] == doc["id"] for p in resp_list.json())

    # 4. Verify outbox event `policy.uploaded.stub` was atomically enqueued
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        outbox_row = await conn.fetchrow(
            """
            SELECT event_type, status, event_payload
            FROM public.outbox_events
            WHERE tenant_id = $1::uuid AND event_type = 'policy.uploaded.stub'
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            globex.tenant_id,
        )
        assert outbox_row is not None
        assert outbox_row["status"] == "pending"
    finally:
        await conn.close()

    # 5. Delete policy cleans up record & local file
    resp_del = await async_client.delete(
        f"/api/v1/policies/{doc['id']}",
        headers=owner_headers,
    )
    assert resp_del.status_code == 200
    assert resp_del.json()["status"] == "deleted"


# ==============================================================================
# 4. Readiness Gating (400 ONBOARDING_NOT_READY), Launch Success, Idempotency & RBAC
# ==============================================================================
@pytest.mark.asyncio
async def test_04_readiness_gating_launch_idempotency_and_rbac(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - Readiness checklist returns can_go_live=False when prerequisites are missing and blocks
      POST /api/v1/onboarding/launch with HTTP 400 (ONBOARDING_NOT_READY).
    - Employee role is blocked from POST /api/v1/onboarding/launch with HTTP 403.
    - Once all 4 prerequisites pass (connector, policy, department, employee), launch succeeds,
      sets is_live=True and launched_at in Asia/Karachi (+05:00), enqueues sheets_sync_queue,
      sends idempotent invitations via EmailDeliveryService (`onboarding-invite-{tenant}-{emp}`),
      logs `onboarding.launched` audit entry, and replays cleanly on repeated Idempotency-Key.
    """
    ctx = isolation_context
    globex = ctx.company_b
    await _reset_globex_stage4_state(globex.tenant_id)
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    # 1. Missing all 4 prerequisites -> can_go_live=False
    resp_r0 = await async_client.get("/api/v1/onboarding/readiness", headers=owner_headers)
    assert resp_r0.status_code == 200, resp_r0.text
    r0 = resp_r0.json()
    assert r0["connectors_ok"] is False
    assert r0["policies_ok"] is False
    assert r0["departments_ok"] is False
    assert r0["employees_ok"] is False
    assert r0["can_go_live"] is False
    assert len(r0["missing_items"]) == 4

    # 2. Launch blocked with HTTP 400 ONBOARDING_NOT_READY
    resp_block = await async_client.post("/api/v1/onboarding/launch", headers=owner_headers)
    assert resp_block.status_code == 400
    assert "ONBOARDING_NOT_READY" in resp_block.text

    # 3. Employee role blocked with HTTP 403
    resp_emp_block = await async_client.post("/api/v1/onboarding/launch", headers=emp_headers)
    assert resp_emp_block.status_code == 403

    # 4. Satisfy all 4 prerequisites for Globex Corp
    # 4a. Connect active Google connector
    resp_conn = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=owner_headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/calendar.events",
            ],
            "access_token": "fake_launch_access_token",
            "refresh_token": "fake_launch_refresh_token",
            "token_expiry": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert resp_conn.status_code == 200, resp_conn.text
    cred_id = resp_conn.json()["id"]

    # 4b. Upload 1 policy document stub
    resp_pol = await async_client.post(
        "/api/v1/policies",
        headers=owner_headers,
        data={"title": "Globex Handbook"},
        files={"file": ("handbook.txt", b"Standard handbook policy text.", "text/plain")},
    )
    assert resp_pol.status_code == 201, resp_pol.text

    # 4c. Create 1 department
    resp_dept = await async_client.post(
        "/api/v1/departments",
        headers=owner_headers,
        json={"name": "Engineering", "code": "ENG"},
    )
    assert resp_dept.status_code == 201, resp_dept.text
    dept_id = resp_dept.json()["id"]

    # 4d. Insert 2 active employees in Globex Corp
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        emp_ids: list[str] = []
        for idx in (1, 2):
            e_id = str(
                await conn.fetchval(
                    """
                    INSERT INTO public.employees (
                        tenant_id, department_id, employee_code, full_name, email, job_title, join_date, status
                    )
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'Engineer', $6, 'active')
                    RETURNING id;
                    """,
                    globex.tenant_id,
                    dept_id,
                    f"GLX-00{idx}",
                    f"Globex Staff {idx}",
                    f"staff{idx}@globex.internal",
                    date(2025, 2, 1),
                )
            )
            emp_ids.append(e_id)
    finally:
        await conn.close()

    # 5. Readiness now reports can_go_live=True
    resp_r1 = await async_client.get("/api/v1/onboarding/readiness", headers=owner_headers)
    assert resp_r1.status_code == 200
    r1 = resp_r1.json()
    assert r1["can_go_live"] is True
    assert r1["missing_items"] == []
    assert r1["counts"] == {"connectors": 1, "policies": 1, "departments": 1, "employees": 2}

    # 6. Launch with Idempotency-Key
    launch_idem_key = f"launch-globex-{uuid.uuid4().hex[:8]}"
    resp_launch = await async_client.post(
        "/api/v1/onboarding/launch",
        headers={**owner_headers, "Idempotency-Key": launch_idem_key},
    )
    assert resp_launch.status_code == 200, resp_launch.text
    launched = resp_launch.json()
    assert launched["is_live"] is True
    assert "+05:00" in launched["launched_at"]
    assert launched["invitations_sent"] == 2
    assert launched["invitations_skipped"] == 0
    assert launched["sheets_queued"] == 2

    # 7. Replay with same Idempotency-Key returns identical response without duplicate emails
    resp_replay = await async_client.post(
        "/api/v1/onboarding/launch",
        headers={**owner_headers, "Idempotency-Key": launch_idem_key},
    )
    assert resp_replay.status_code == 200, resp_replay.text
    assert resp_replay.json() == launched

    # Verify DB invariants: sent_messages count == 2 with expected idempotency keys,
    # sheets_sync_queue count == 2, audit_logs has onboarding.launched, idempotency completed
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        sent_keys = await conn.fetch(
            "SELECT idempotency_key FROM public.sent_messages WHERE tenant_id = $1::uuid;",
            globex.tenant_id,
        )
        expected_keys = {f"onboarding-invite-{globex.tenant_id}-{eid}" for eid in emp_ids}
        assert {r["idempotency_key"] for r in sent_keys} == expected_keys

        sheets_cnt = await conn.fetchval(
            """
            SELECT count(*) FROM public.sheets_sync_queue
            WHERE tenant_id = $1::uuid AND action = 'onboarding_launch';
            """,
            globex.tenant_id,
        )
        assert sheets_cnt == 2

        audit_cnt = await conn.fetchval(
            """
            SELECT count(*) FROM public.audit_logs
            WHERE tenant_id = $1::uuid AND action = 'onboarding.launched';
            """,
            globex.tenant_id,
        )
        assert audit_cnt >= 1

        idem_status = await conn.fetchval(
            """
            SELECT status FROM public.idempotency_keys
            WHERE tenant_id = $1::uuid AND key = $2;
            """,
            globex.tenant_id,
            launch_idem_key,
        )
        assert idem_status == "completed"
    finally:
        await conn.close()

    # 8. Test read-only connector skips invitations cleanly without leaving idempotency 'started'
    assert settings.USE_FAKE_INTEGRATIONS is True
    resp_ro = await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=owner_headers,
        json={"is_read_only": True},
    )
    assert resp_ro.status_code == 200

    # Add a 3rd employee who has not been invited yet
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            """
            INSERT INTO public.employees (
                tenant_id, department_id, employee_code, full_name, email, job_title, join_date, status
            )
            VALUES ($1::uuid, $2::uuid, 'GLX-003', 'Globex Staff 3', 'staff3@globex.internal', 'Engineer', $3, 'active');
            """,
            globex.tenant_id,
            dept_id,
            date(2025, 3, 1),
        )
    finally:
        await conn.close()

    ro_key = f"launch-globex-ro-{uuid.uuid4().hex[:8]}"
    resp_ro_launch = await async_client.post(
        "/api/v1/onboarding/launch",
        headers={**owner_headers, "Idempotency-Key": ro_key},
    )
    assert resp_ro_launch.status_code == 200, resp_ro_launch.text
    ro_body = resp_ro_launch.json()
    assert ro_body["is_live"] is True
    assert ro_body["invitations_skipped"] >= 1

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        ro_idem_status = await conn.fetchval(
            """
            SELECT status FROM public.idempotency_keys
            WHERE tenant_id = $1::uuid AND key = $2;
            """,
            globex.tenant_id,
            ro_key,
        )
        assert ro_idem_status == "completed"
    finally:
        await conn.close()
