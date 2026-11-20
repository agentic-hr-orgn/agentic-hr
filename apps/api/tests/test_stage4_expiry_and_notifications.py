"""
Stage 4 Prompt 5 — Daily Temporal Document Expiry Schedule (30/7/1 Days),
In-App Notifications Engine & Role-Based Visibility Tests.

Verifies:
1. Sandboxed `DocumentExpiryScanWorkflow` via `WorkflowEnvironment.start_time_skipping()`:
   - Matches ONLY documents expiring in 30, 7, and 1 days for `active` / `on_leave` employees.
   - Ignores documents expiring in 15 or 60 days and documents belonging to `terminated` employees.
   - Assigns severities `info` (30d), `warning` (7d), and `critical` (1d).
   - Creates per-recipient in-app `notifications` and `sent_messages` (`document_expiry_alert`)
     for active Owner + HR Head + linked employee (`user_id IS NOT NULL`), never emailing unlinked
     `.internal` addresses.
2. Full idempotency on re-scan:
   - Re-running the workflow on the same reference date creates 0 duplicate notifications and
     0 duplicate `sent_messages` (`dedupe_key` + `IdempotencyGate`), leaving all `email.send`
     idempotency records in `completed`.
3. Read-only connector resilience:
   - When `is_read_only=true`, in-app notifications are still persisted cleanly while outbound
     Gmail alerts are skipped without crashing or leaving `started` idempotency records.
4. Notifications REST API & strict role-based visibility:
   - `GET /api/v1/notifications`, `PATCH /{id}/read`, `POST /read-all`, `POST /trigger-expiry-scan`.
   - Employee sees only their own notifications and receives 404 when attempting to mark an
     Owner/HR Head notification as read.
   - Cross-tenant isolation (Acme owner gets 404 on Globex notification ID).
   - Plain manager gets 403 on `trigger-expiry-scan`; Owner gets 200.
   - Timestamps normalized to `Asia/Karachi` (`+05:00`).
5. Idempotent Temporal schedule helper (`ensure_daily_document_expiry_schedule`):
   - Verified with a lightweight mock Temporal client: first call invokes `create_schedule` with
     cron `"0 9 * * *"` and `time_zone_name="Asia/Karachi"`; second call catches
     `ScheduleAlreadyRunningError` and invokes `handle.update(...)`.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import asyncpg
import httpx
import pytest
import pytest_asyncio
from temporalio.client import Schedule, ScheduleAlreadyRunningError, ScheduleUpdateInput
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.core.encryption import EncryptionContext, encrypt_field
from app.db.session import AsyncSessionLocal
from app.services.document_expiry_service import DocumentExpiryService
from app.worker import (
    DAILY_DOCUMENT_EXPIRY_CRON,
    DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID,
    DAILY_DOCUMENT_EXPIRY_TZ,
    ensure_daily_document_expiry_schedule,
)
from app.workflows import (
    DocumentExpiryScanInput,
    DocumentExpiryScanWorkflow,
    list_active_tenant_ids_activity,
    scan_tenant_expiring_documents_activity,
)
from tests.isolation_harness import MIGRATION_DB_URL, MultiTenantTestContext

REF_DATE = date(2030, 6, 1)


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


async def _setup_globex_expiry_fixture(
    globex_tenant_id: str,
    owner_user_id: str,
    manager_membership_id: str,
    employee_user_id: str,
    *,
    is_read_only: bool = False,
) -> dict[str, str]:
    """
    Resets Globex Corp's employees, documents, notifications, sent_messages, idempotency_keys,
    and connector_credentials. Seeds:
    - 1 active employee linked to `employee_user_id` with docs at +30d, +7d, +1d, +15d, +60d.
    - 1 terminated employee (unlinked) with a doc at +7d (must be ignored).
    - 1 connected Google connector with future token_expiry and encrypted refresh token.
    - Sets `globex.manager` membership `is_hr_head = true` so Owner + HR Head + linked employee
      are all active recipients (3 distinct users).
    """
    cred_id = uuid.uuid4()
    enc_access = encrypt_field(
        "fake-access-token-expiry-stage4",
        EncryptionContext(
            tenant_id=globex_tenant_id,
            entity_type="connector_credential",
            record_id=cred_id,
            field_name="access_token",
        ),
    )
    enc_refresh = encrypt_field(
        "fake-refresh-token-expiry-stage4",
        EncryptionContext(
            tenant_id=globex_tenant_id,
            entity_type="connector_credential",
            record_id=cred_id,
            field_name="refresh_token",
        ),
    )

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "notifications",
            "sent_messages",
            "idempotency_keys",
            "employee_documents",
            "connector_credentials",
            "employees",
            "departments",
        ):
            await conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                globex_tenant_id,
            )

        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = true, is_payroll_admin = false "
            "WHERE id = $1::uuid;",
            manager_membership_id,
        )

        dept_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.departments (tenant_id, name, code)
                VALUES ($1::uuid, 'Compliance', 'CMP')
                RETURNING id;
                """,
                globex_tenant_id,
            )
        )

        active_emp_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.employees (
                    tenant_id, user_id, employee_code, full_name, email,
                    department_id, job_title, employment_type, join_date, status
                )
                VALUES (
                    $1::uuid, $2::uuid, 'GLX-EXP-01', 'Tariq Mahmood',
                    'tariq.mahmood@globex.internal', $3::uuid,
                    'Compliance Analyst', 'full_time', '2025-01-01', 'active'
                )
                RETURNING id;
                """,
                globex_tenant_id,
                employee_user_id,
                dept_id,
            )
        )

        terminated_emp_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.employees (
                    tenant_id, user_id, employee_code, full_name, email,
                    department_id, job_title, employment_type, join_date, status
                )
                VALUES (
                    $1::uuid, NULL, 'GLX-EXP-02', 'Former Staff',
                    'former.staff@globex.internal', $2::uuid,
                    'Former Analyst', 'full_time', '2024-01-01', 'terminated'
                )
                RETURNING id;
                """,
                globex_tenant_id,
                dept_id,
            )
        )

        doc_ids: dict[str, str] = {}
        doc_specs = [
            ("doc_30d", active_emp_id, "cnic", "CNIC Renewal", REF_DATE + timedelta(days=30)),
            ("doc_7d", active_emp_id, "contract", "Annual Contract", REF_DATE + timedelta(days=7)),
            (
                "doc_1d",
                active_emp_id,
                "certificate",
                "Security Clearance",
                REF_DATE + timedelta(days=1),
            ),
            ("doc_15d", active_emp_id, "other", "Parking Permit", REF_DATE + timedelta(days=15)),
            ("doc_60d", active_emp_id, "certificate", "ISO Cert", REF_DATE + timedelta(days=60)),
            (
                "doc_term_7d",
                terminated_emp_id,
                "contract",
                "Terminated Contract",
                REF_DATE + timedelta(days=7),
            ),
        ]
        for key, emp_id, doc_type, title, exp_date in doc_specs:
            doc_ids[key] = str(
                await conn.fetchval(
                    """
                    INSERT INTO public.employee_documents (
                        tenant_id, employee_id, document_type, title,
                        file_name, mime_type, size_bytes,
                        storage_provider, storage_key, expiry_date, uploaded_by
                    )
                    VALUES (
                        $1::uuid, $2::uuid, $3, $4,
                        'doc.pdf', 'application/pdf', 1024,
                        'local', $5, $6, $7::uuid
                    )
                    RETURNING id;
                    """,
                    globex_tenant_id,
                    emp_id,
                    doc_type,
                    title,
                    f"{globex_tenant_id}/docs/{key}.pdf",
                    exp_date,
                    owner_user_id,
                )
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
                '["https://www.googleapis.com/auth/gmail.send"]'::jsonb,
                $3, $4, NOW() + INTERVAL '2 hours', 'connected', $5
            );
            """,
            str(cred_id),
            globex_tenant_id,
            enc_access,
            enc_refresh,
            is_read_only,
        )

        return {
            "dept_id": dept_id,
            "active_emp_id": active_emp_id,
            "terminated_emp_id": terminated_emp_id,
            **doc_ids,
        }
    finally:
        await conn.close()


# ==============================================================================
# 1. Sandboxed Temporal Workflow (Time-Skipping): 30/7/1 Days Only + Emails
# ==============================================================================
@pytest.mark.asyncio
async def test_01_workflow_time_skipping_creates_alerts_for_30_7_1_only(
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    ids = await _setup_globex_expiry_fixture(
        globex.tenant_id,
        globex.owner.user_id,
        globex.manager.membership_id,
        globex.employee.user_id,
        is_read_only=False,
    )

    env = await WorkflowEnvironment.start_time_skipping()
    task_queue = "test-doc-expiry-queue-01"
    async with (
        env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=[DocumentExpiryScanWorkflow],
            activities=[
                list_active_tenant_ids_activity,
                scan_tenant_expiring_documents_activity,
            ],
        ),
    ):
        result = await env.client.execute_workflow(
            DocumentExpiryScanWorkflow.run,
            DocumentExpiryScanInput(
                reference_date_iso=REF_DATE.isoformat(),
                tenant_ids=[globex.tenant_id],
            ),
            id=f"test-doc-expiry-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )

    assert result["reference_date"] == REF_DATE.isoformat()
    assert result["tenants_scanned"] == 1
    assert result["failed_tenants"] == []
    # 3 matching docs (30d, 7d, 1d) x 3 distinct recipients (Owner, HR Head, linked employee) = 9
    assert result["alerts_created"] == 9
    assert result["alerts_deduplicated"] == 0
    assert result["emails_sent"] == 9
    assert result["emails_skipped"] == 0

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        notif_rows = await conn.fetch(
            """
            SELECT entity_id::text AS entity_id, severity, recipient_email
            FROM public.notifications
            WHERE tenant_id = $1::uuid AND category = 'document_expiry';
            """,
            globex.tenant_id,
        )
        assert len(notif_rows) == 9

        by_doc: dict[str, set[str]] = {}
        for row in notif_rows:
            by_doc.setdefault(row["entity_id"], set()).add(row["severity"])

        assert by_doc[ids["doc_30d"]] == {"info"}
        assert by_doc[ids["doc_7d"]] == {"warning"}
        assert by_doc[ids["doc_1d"]] == {"critical"}
        assert ids["doc_15d"] not in by_doc
        assert ids["doc_60d"] not in by_doc
        assert ids["doc_term_7d"] not in by_doc

        sent_rows = await conn.fetch(
            """
            SELECT recipient_email, template_name, status
            FROM public.sent_messages
            WHERE tenant_id = $1::uuid AND template_name = 'document_expiry_alert';
            """,
            globex.tenant_id,
        )
        assert len(sent_rows) == 9
        assert all(r["status"] == "sent" for r in sent_rows)
        # Never emails unlinked .internal addresses
        assert all(not r["recipient_email"].endswith(".internal") for r in sent_rows)
    finally:
        await conn.close()


# ==============================================================================
# 2. Re-Running Workflow Is Fully Idempotent (0 Duplicate Notifications & Emails)
# ==============================================================================
@pytest.mark.asyncio
async def test_02_rescan_is_fully_idempotent(
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    await _setup_globex_expiry_fixture(
        globex.tenant_id,
        globex.owner.user_id,
        globex.manager.membership_id,
        globex.employee.user_id,
        is_read_only=False,
    )

    env = await WorkflowEnvironment.start_time_skipping()
    task_queue = "test-doc-expiry-queue-02"
    async with (
        env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=[DocumentExpiryScanWorkflow],
            activities=[
                list_active_tenant_ids_activity,
                scan_tenant_expiring_documents_activity,
            ],
        ),
    ):
        first = await env.client.execute_workflow(
            DocumentExpiryScanWorkflow.run,
            DocumentExpiryScanInput(
                reference_date_iso=REF_DATE.isoformat(),
                tenant_ids=[globex.tenant_id],
            ),
            id=f"test-doc-expiry-run1-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
        second = await env.client.execute_workflow(
            DocumentExpiryScanWorkflow.run,
            DocumentExpiryScanInput(
                reference_date_iso=REF_DATE.isoformat(),
                tenant_ids=[globex.tenant_id],
            ),
            id=f"test-doc-expiry-run2-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )

    assert first["alerts_created"] == 9
    assert first["emails_sent"] == 9
    assert second["alerts_created"] == 0
    assert second["alerts_deduplicated"] == 9
    assert second["emails_sent"] == 0
    assert second["emails_skipped"] == 0

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        notif_count = await conn.fetchval(
            "SELECT COUNT(*) FROM public.notifications WHERE tenant_id = $1::uuid;",
            globex.tenant_id,
        )
        sent_count = await conn.fetchval(
            "SELECT COUNT(*) FROM public.sent_messages WHERE tenant_id = $1::uuid;",
            globex.tenant_id,
        )
        idem_statuses = await conn.fetch(
            """
            SELECT status FROM public.idempotency_keys
            WHERE tenant_id = $1::uuid AND operation = 'email.send';
            """,
            globex.tenant_id,
        )
        assert notif_count == 9
        assert sent_count == 9
        assert len(idem_statuses) == 9
        assert all(r["status"] == "completed" for r in idem_statuses)
    finally:
        await conn.close()


# ==============================================================================
# 3. Read-Only Connector Keeps In-App Notifications Intact Without Crashing
# ==============================================================================
@pytest.mark.asyncio
async def test_03_read_only_connector_keeps_in_app_notifications(
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    await _setup_globex_expiry_fixture(
        globex.tenant_id,
        globex.owner.user_id,
        globex.manager.membership_id,
        globex.employee.user_id,
        is_read_only=True,
    )

    async with AsyncSessionLocal() as session:
        summary = await DocumentExpiryService.scan_expiring_documents_for_tenant(
            session=session,
            tenant_id=globex.tenant_id,
            reference_date_pkt=REF_DATE,
            send_emails=True,
        )

    assert summary.documents_matched == 3
    assert summary.alerts_created == 9
    assert summary.emails_sent == 0
    assert summary.emails_skipped == 9

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        notif_count = await conn.fetchval(
            "SELECT COUNT(*) FROM public.notifications WHERE tenant_id = $1::uuid;",
            globex.tenant_id,
        )
        sent_count = await conn.fetchval(
            "SELECT COUNT(*) FROM public.sent_messages WHERE tenant_id = $1::uuid;",
            globex.tenant_id,
        )
        started_idem = await conn.fetchval(
            """
            SELECT COUNT(*) FROM public.idempotency_keys
            WHERE tenant_id = $1::uuid AND status = 'started';
            """,
            globex.tenant_id,
        )
        assert notif_count == 9
        assert sent_count == 0
        assert started_idem == 0
    finally:
        await conn.close()


# ==============================================================================
# 4. Notifications REST API, Role-Based Visibility & Cross-Tenant Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_04_notifications_api_list_mark_read_and_rbac(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    acme = isolation_context.company_a
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    manager_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    employee_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    acme_headers = {"Authorization": f"Bearer {acme.owner.token}"}

    await _setup_globex_expiry_fixture(
        globex.tenant_id,
        globex.owner.user_id,
        globex.manager.membership_id,
        globex.employee.user_id,
        is_read_only=False,
    )

    # Demote manager to plain manager (is_hr_head=false) to verify 403 on manual trigger
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
            globex.manager.membership_id,
        )
    finally:
        await conn.close()

    resp_mgr_trigger = await async_client.post(
        "/api/v1/notifications/trigger-expiry-scan",
        json={"reference_date": REF_DATE.isoformat()},
        headers=manager_headers,
    )
    assert resp_mgr_trigger.status_code == 403

    # Owner triggers manual scan synchronously
    resp_owner_trigger = await async_client.post(
        "/api/v1/notifications/trigger-expiry-scan",
        json={"reference_date": REF_DATE.isoformat()},
        headers=owner_headers,
    )
    assert resp_owner_trigger.status_code == 200, resp_owner_trigger.text
    scan_json = resp_owner_trigger.json()
    assert scan_json["documents_matched"] == 3
    # 3 docs x 2 recipients (Owner + linked employee; manager is no longer hr_head) = 6
    assert scan_json["alerts_created"] == 6
    assert scan_json["emails_sent"] == 6

    # Owner lists all 6 tenant notifications; verify +05:00 PKT timestamps
    resp_owner_list = await async_client.get("/api/v1/notifications", headers=owner_headers)
    assert resp_owner_list.status_code == 200, resp_owner_list.text
    owner_list = resp_owner_list.json()
    assert owner_list["total"] == 6
    assert owner_list["unread_count"] == 6
    assert all(item["created_at"].endswith("+05:00") for item in owner_list["items"])

    # Employee sees ONLY their own 3 notifications
    resp_emp_list = await async_client.get("/api/v1/notifications", headers=employee_headers)
    assert resp_emp_list.status_code == 200
    emp_list = resp_emp_list.json()
    assert emp_list["total"] == 3
    assert emp_list["unread_count"] == 3
    assert all(item["recipient_user_id"] == globex.employee.user_id for item in emp_list["items"])

    # Find an Owner-addressed notification ID and verify Employee CANNOT mark it read (404)
    owner_only_notif = next(
        item for item in owner_list["items"] if item["recipient_user_id"] == globex.owner.user_id
    )
    resp_emp_forbidden = await async_client.patch(
        f"/api/v1/notifications/{owner_only_notif['id']}/read",
        headers=employee_headers,
    )
    assert resp_emp_forbidden.status_code == 404

    # Cross-tenant isolation: Acme owner gets 404 on Globex notification ID
    resp_acme_cross = await async_client.patch(
        f"/api/v1/notifications/{owner_only_notif['id']}/read",
        headers=acme_headers,
    )
    assert resp_acme_cross.status_code == 404

    # Employee marks one of their own notifications read
    emp_notif_id = emp_list["items"][0]["id"]
    resp_emp_read = await async_client.patch(
        f"/api/v1/notifications/{emp_notif_id}/read",
        headers=employee_headers,
    )
    assert resp_emp_read.status_code == 200
    assert resp_emp_read.json()["is_read"] is True
    assert resp_emp_read.json()["read_at"].endswith("+05:00")

    # Employee marks all their remaining visible notifications read (must NOT affect Owner's)
    resp_emp_read_all = await async_client.post(
        "/api/v1/notifications/read-all",
        headers=employee_headers,
    )
    assert resp_emp_read_all.status_code == 200
    assert resp_emp_read_all.json()["updated"] == 2

    resp_owner_after_emp = await async_client.get("/api/v1/notifications", headers=owner_headers)
    assert resp_owner_after_emp.json()["unread_count"] == 3

    # Owner marks all remaining tenant notifications read
    resp_owner_read_all = await async_client.post(
        "/api/v1/notifications/read-all",
        headers=owner_headers,
    )
    assert resp_owner_read_all.status_code == 200
    assert resp_owner_read_all.json()["updated"] == 3

    resp_final = await async_client.get(
        "/api/v1/notifications?unread_only=true",
        headers=owner_headers,
    )
    assert resp_final.json()["total"] == 0
    assert resp_final.json()["unread_count"] == 0


# ==============================================================================
# 5. Idempotent Daily Schedule Helper (Mock Temporal Client — 09:00 Asia/Karachi)
# ==============================================================================
@pytest.mark.asyncio
async def test_05_schedule_helper_is_idempotent() -> None:
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
            self.create_calls = 0

        async def create_schedule(self, schedule_id: str, schedule: Schedule) -> None:
            self.create_calls += 1
            if schedule_id in self.schedules:
                raise ScheduleAlreadyRunningError()
            self.schedules[schedule_id] = schedule

        def get_schedule_handle(self, schedule_id: str) -> _FakeScheduleHandle:
            if schedule_id not in self.handles:
                self.handles[schedule_id] = _FakeScheduleHandle(self.schedules, schedule_id)
            return self.handles[schedule_id]

    fake_client = _FakeTemporalClient()

    first_outcome = await ensure_daily_document_expiry_schedule(fake_client)  # type: ignore[arg-type]
    second_outcome = await ensure_daily_document_expiry_schedule(fake_client)  # type: ignore[arg-type]

    assert first_outcome == "created"
    assert second_outcome == "updated"
    assert fake_client.create_calls == 2
    assert fake_client.handles[DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID].update_calls == 1

    sched = fake_client.schedules[DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID]
    assert list(sched.spec.cron_expressions) == [DAILY_DOCUMENT_EXPIRY_CRON]
    assert sched.spec.time_zone_name == DAILY_DOCUMENT_EXPIRY_TZ
