"""
Stage 7 Prompt 2 Verification Suite:
1. Ticket creation, sequential numbering, category assignment rules & HR/Owner RBAC.
2. Employee vs. staff visibility, internal notes hidden from employees (no email),
   staff public reply notifications, and `pub_ticket_summary` (`GET /api/v1/tickets/summary`).
3. Resolve, close preservation of `resolved_at` / `reopen_until`, requester rating (1..5),
   reopen within window vs. `409 TICKET_REOPEN_WINDOW_EXPIRED`.
4. Read-only Helpdesk Agent (`POST /api/v1/helpdesk/ask`):
   - Verified `helpdesk_kb` citations without `hr_only` leakage.
   - Auto-created routed tickets on unknown questions & blocked privileged actions.
5. Time-skipping Temporal `TicketSLAWorkflow` (pre-breach reminder -> breach escalation
   + `ticket_resolved` signal cancellation) wrapped safely in `try ... finally: set_temporal_client(None)`.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.core.encryption import EncryptionContext, encrypt_field
from app.core.temporal import reset_temporal_client, set_temporal_client
from app.services.storage_helper import StorageHelper
from app.workflows import (
    TicketSLAWorkflow,
    escalate_ticket_sla_breach_activity,
    send_ticket_sla_reminder_activity,
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


async def _seed_globex_google_connector(tenant_id: str) -> None:
    """Seeds an active fake Google connector credential so EmailDeliveryService records sent_messages."""
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            "DELETE FROM public.connector_credentials WHERE tenant_id = $1::uuid;",
            tenant_id,
        )
        cred_id = uuid.uuid4()
        enc_acc = encrypt_field(
            "fake-acc-s7",
            EncryptionContext(tenant_id, "connector_credential", cred_id, "access_token"),
        )
        enc_ref = encrypt_field(
            "fake-ref-s7",
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
                $1::uuid, $2::uuid, 'google', 'helpdesk@globex.internal',
                '["https://www.googleapis.com/auth/gmail.send"]'::jsonb,
                $3, $4, NOW() + INTERVAL '2 hours', 'connected', false
            );
            """,
            str(cred_id),
            tenant_id,
            enc_acc,
            enc_ref,
        )
    finally:
        await conn.close()


async def _cleanup_globex_stage7(
    tenant_id: str,
    *,
    policy_ids: list[str] | None = None,
    approval_ids: list[str] | None = None,
    storage_keys: list[str] | None = None,
) -> None:
    """Deterministically cleans up all Stage 7 tickets, rules, KB docs, emails, and outbox rows."""
    for key in storage_keys or []:
        StorageHelper.delete_local_file(tenant_id=tenant_id, storage_key=key)

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "answer_feedback",
            "chat_messages",
            "chat_sessions",
            "ticket_messages",
            "tickets",
            "ticket_assignment_rules",
            "notifications",
            "sent_messages",
            "idempotency_keys",
            "outbox_events",
            "connector_credentials",
        ):
            await conn.execute(
                f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;",
                tenant_id,
            )
        if policy_ids:
            await conn.execute(
                "DELETE FROM public.policy_documents WHERE id = ANY($1::uuid[]);",
                policy_ids,
            )
        if approval_ids:
            await conn.execute(
                "DELETE FROM public.approvals WHERE id = ANY($1::uuid[]);",
                approval_ids,
            )
    finally:
        await conn.close()


async def _upload_and_publish_doc(
    client: httpx.AsyncClient,
    *,
    owner_headers: dict[str, str],
    title: str,
    content: bytes,
    doc_type: str,
    visibility: str = "all",
) -> tuple[str, str, str]:
    """Helper uploading and publishing a policy/KB `.txt` document via Stage 5 lifecycle API."""
    r_up = await client.post(
        "/api/v1/policies",
        headers=owner_headers,
        data={
            "title": title,
            "doc_type": doc_type,
            "visibility": visibility,
        },
        files={"file": (f"{title.lower().replace(' ', '_')}.txt", content, "text/plain")},
    )
    assert r_up.status_code == 201, r_up.text
    doc = r_up.json()
    doc_id = doc["id"]
    storage_key = doc["storage_key"]

    r_pub = await client.post(f"/api/v1/policies/{doc_id}/publish", headers=owner_headers)
    assert r_pub.status_code == 200, r_pub.text
    approval_id = r_pub.json()["approval_id"]

    r_app = await client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers=owner_headers,
        json={"reason": "Approved for Stage 7 Helpdesk KB test"},
    )
    assert r_app.status_code == 200, r_app.text
    return doc_id, approval_id, storage_key


# ==============================================================================
# 1. Category Auto-Assignment, Sequential Numbering & Assignment Rules RBAC
# ==============================================================================
@pytest.mark.asyncio
async def test_01_ticket_creation_auto_assignment_and_rules_rbac(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _cleanup_globex_stage7(globex.tenant_id)
    await _seed_globex_google_connector(globex.tenant_id)

    try:
        # Ensure globex.manager is tested as a plain manager first (`_preserve_globex_manager_flags` restores)
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
        finally:
            await conn.close()

        # 1. Plain employee and plain manager cannot read or modify assignment rules (403)
        assert (
            await async_client.get("/api/v1/tickets/rules", headers=emp_headers)
        ).status_code == 403
        assert (
            await async_client.get("/api/v1/tickets/rules", headers=mgr_headers)
        ).status_code == 403
        assert (
            await async_client.put(
                "/api/v1/tickets/rules/it_access",
                headers=emp_headers,
                json={
                    "default_assignee_role": "owner",
                    "sla_hours": 12,
                    "reminder_before_hours": 2,
                    "reopen_window_days": 7,
                },
            )
        ).status_code == 403

        # 2. Owner can list default rules and customize `it_access` -> assign to globex.manager
        r_rules = await async_client.get("/api/v1/tickets/rules", headers=owner_headers)
        assert r_rules.status_code == 200, r_rules.text
        assert {r["category"] for r in r_rules.json()} == {
            "hr_policy",
            "leave_attendance",
            "payroll",
            "it_access",
            "facilities",
            "general",
        }

        r_put = await async_client.put(
            "/api/v1/tickets/rules/it_access",
            headers=owner_headers,
            json={
                "default_assignee_user_id": globex.manager.user_id,
                "default_assignee_role": "manager",
                "sla_hours": 8,
                "reminder_before_hours": 2,
                "reopen_window_days": 5,
            },
        )
        assert r_put.status_code == 200, r_put.text
        assert r_put.json()["default_assignee_user_id"] == globex.manager.user_id

        # Also verify an HR Head (non-owner) is allowed by require_hr_head
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = true WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
        finally:
            await conn.close()
        assert (
            await async_client.get("/api/v1/tickets/rules", headers=mgr_headers)
        ).status_code == 200

        # 3. Employee creates two tickets -> sequential TKT-YYYY-0001 / 0002 & auto-assignment
        t1 = await async_client.post(
            "/api/v1/tickets",
            headers=emp_headers,
            json={
                "category": "it_access",
                "priority": "high",
                "subject": "Need GitHub repo access",
                "description": "Please grant access to agentic-hr repo.",
                "start_workflow": False,
            },
        )
        assert t1.status_code == 201, t1.text
        d1 = t1.json()
        assert d1["ticket_number"].endswith("-0001")
        assert d1["assigned_to_user_id"] == globex.manager.user_id
        assert d1["source"] == "manual"

        t2 = await async_client.post(
            "/api/v1/tickets",
            headers=emp_headers,
            json={
                "category": "hr_policy",
                "priority": "medium",
                "subject": "Medical reimbursement limit",
                "description": "Checking outpatient reimbursement cap.",
                "start_workflow": False,
            },
        )
        assert t2.status_code == 201, t2.text
        d2 = t2.json()
        assert d2["ticket_number"].endswith("-0002")

        # 4. Verify outbox `ticket.created` and fake email `sent_messages` rows
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            outbox_cnt = await conn.fetchval(
                "SELECT count(*) FROM public.outbox_events WHERE tenant_id = $1::uuid AND event_type = 'ticket.created';",
                globex.tenant_id,
            )
            assert outbox_cnt == 2

            sent_rows = await conn.fetch(
                "SELECT recipient_email, template_name, status FROM public.sent_messages WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
            assert len(sent_rows) >= 1
            assert any(
                r["recipient_email"] == globex.manager.email.lower()
                and r["template_name"] == "ticket_created"
                and r["status"] == "sent"
                for r in sent_rows
            )
        finally:
            await conn.close()
    finally:
        await _cleanup_globex_stage7(globex.tenant_id)


# ==============================================================================
# 2. Employee vs. Staff Visibility, Internal Notes & `pub_ticket_summary`
# ==============================================================================
@pytest.mark.asyncio
async def test_02_employee_vs_staff_visibility_internal_notes_and_summary(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _cleanup_globex_stage7(globex.tenant_id)
    await _seed_globex_google_connector(globex.tenant_id)

    try:
        # Employee creates own ticket; Owner creates another ticket
        r_emp = await async_client.post(
            "/api/v1/tickets",
            headers=emp_headers,
            json={
                "category": "payroll",
                "priority": "medium",
                "subject": "Payslip tax deduction query",
                "description": "Monthly withholding tax looks slightly higher.",
                "start_workflow": False,
            },
        )
        assert r_emp.status_code == 201, r_emp.text
        emp_tkt_id = r_emp.json()["id"]

        r_own = await async_client.post(
            "/api/v1/tickets",
            headers=owner_headers,
            json={
                "category": "general",
                "priority": "low",
                "subject": "Owner internal HR audit",
                "description": "Confidential payroll audit review.",
                "start_workflow": False,
            },
        )
        assert r_own.status_code == 201, r_own.text
        own_tkt_id = r_own.json()["id"]

        # Employee sees ONLY own ticket, blocked from staff queue (403) and owner's ticket (403)
        r_me = await async_client.get("/api/v1/tickets/me", headers=emp_headers)
        assert r_me.status_code == 200
        assert [t["id"] for t in r_me.json()] == [emp_tkt_id]

        assert (
            await async_client.get("/api/v1/tickets/queue", headers=emp_headers)
        ).status_code == 403
        assert (
            await async_client.get(f"/api/v1/tickets/{own_tkt_id}", headers=emp_headers)
        ).status_code == 403

        # Staff sees both tickets in queue
        r_queue = await async_client.get("/api/v1/tickets/queue", headers=mgr_headers)
        assert r_queue.status_code == 200
        assert {t["id"] for t in r_queue.json()} == {emp_tkt_id, own_tkt_id}

        # Employee attempting is_internal=True gets forced to False
        r_emp_msg = await async_client.post(
            f"/api/v1/tickets/{emp_tkt_id}/messages",
            headers=emp_headers,
            json={"body": "Employee follow-up detail", "is_internal": True},
        )
        assert r_emp_msg.status_code == 201, r_emp_msg.text
        assert r_emp_msg.json()["is_internal"] is False

        # Staff posts a SECRET internal note -> NO email or notification sent to employee
        secret_internal = "INTERNAL_HR_NOTE_CHECK_SALARY_LEDGER"
        r_int = await async_client.post(
            f"/api/v1/tickets/{emp_tkt_id}/messages",
            headers=owner_headers,
            json={"body": secret_internal, "is_internal": True},
        )
        assert r_int.status_code == 201, r_int.text
        assert r_int.json()["is_internal"] is True

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            emp_emails_after_internal = await conn.fetchval(
                "SELECT count(*) FROM public.sent_messages WHERE tenant_id = $1::uuid AND recipient_email = $2;",
                globex.tenant_id,
                globex.employee.email.lower(),
            )
            assert emp_emails_after_internal == 0
        finally:
            await conn.close()

        # Staff sees internal note, Employee does NOT see internal note
        staff_detail = (
            await async_client.get(f"/api/v1/tickets/{emp_tkt_id}", headers=owner_headers)
        ).json()
        assert any(
            m["body"] == secret_internal and m["is_internal"] for m in staff_detail["messages"]
        )

        emp_detail = (
            await async_client.get(f"/api/v1/tickets/{emp_tkt_id}", headers=emp_headers)
        ).json()
        assert all(not m["is_internal"] for m in emp_detail["messages"])
        assert secret_internal not in repr(emp_detail)

        # Staff posts a public reply -> triggers employee email & in-app notification
        r_pub = await async_client.post(
            f"/api/v1/tickets/{emp_tkt_id}/messages",
            headers=owner_headers,
            json={"body": "We checked your tax slab and updated it.", "is_internal": False},
        )
        assert r_pub.status_code == 201, r_pub.text

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            emp_emails = await conn.fetch(
                "SELECT template_name FROM public.sent_messages WHERE tenant_id = $1::uuid AND recipient_email = $2;",
                globex.tenant_id,
                globex.employee.email.lower(),
            )
            assert [r["template_name"] for r in emp_emails] == ["ticket_public_reply"]

            emp_notif = await conn.fetchval(
                "SELECT count(*) FROM public.notifications WHERE tenant_id = $1::uuid AND recipient_user_id = $2::uuid;",
                globex.tenant_id,
                globex.employee.user_id,
            )
            assert emp_notif == 1
        finally:
            await conn.close()

        # `GET /api/v1/tickets/summary` reads `pub_ticket_summary`:
        # 3 public messages (initial desc + employee reply + staff public reply), excluding internal note
        r_sum_emp = await async_client.get("/api/v1/tickets/summary", headers=emp_headers)
        assert r_sum_emp.status_code == 200, r_sum_emp.text
        sum_rows = r_sum_emp.json()
        assert len(sum_rows) == 1
        assert sum_rows[0]["ticket_id"] == emp_tkt_id
        assert sum_rows[0]["public_message_count"] == 3
        assert secret_internal not in repr(sum_rows)
    finally:
        await _cleanup_globex_stage7(globex.tenant_id)


# ==============================================================================
# 3. Resolve, Close, CSAT Rating & Reopen Window (`409` After Expiry)
# ==============================================================================
@pytest.mark.asyncio
async def test_03_resolve_close_rating_and_reopen_window(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _cleanup_globex_stage7(globex.tenant_id)
    await _seed_globex_google_connector(globex.tenant_id)

    try:
        r_create = await async_client.post(
            "/api/v1/tickets",
            headers=emp_headers,
            json={
                "category": "facilities",
                "priority": "medium",
                "subject": "Office chair replacement",
                "description": "Chair hydraulic cylinder is broken.",
                "start_workflow": False,
            },
        )
        assert r_create.status_code == 201
        tkt_id = r_create.json()["id"]

        # Cannot rate an open ticket (409)
        r_rate_early = await async_client.post(
            f"/api/v1/tickets/{tkt_id}/rate",
            headers=emp_headers,
            json={"satisfaction_rating": 5},
        )
        assert r_rate_early.status_code == 409

        # Staff marks ticket resolved -> sets resolved_at, reopen_until, sends email
        r_res = await async_client.patch(
            f"/api/v1/tickets/{tkt_id}",
            headers=owner_headers,
            json={"status": "resolved"},
        )
        assert r_res.status_code == 200, r_res.text
        d_res = r_res.json()
        assert d_res["status"] == "resolved"
        assert d_res["resolved_at"] is not None
        assert d_res["reopen_until"] is not None

        # Requester rates resolved ticket -> transitions to closed while preserving resolved_at & reopen_until
        r_rate = await async_client.post(
            f"/api/v1/tickets/{tkt_id}/rate",
            headers=emp_headers,
            json={"satisfaction_rating": 5, "satisfaction_comment": "Quick chair swap!"},
        )
        assert r_rate.status_code == 200, r_rate.text
        d_rated = r_rate.json()
        assert d_rated["status"] == "closed"
        assert d_rated["satisfaction_rating"] == 5
        assert d_rated["resolved_at"] == d_res["resolved_at"]
        assert d_rated["reopen_until"] == d_res["reopen_until"]
        assert d_rated["closed_at"] is not None

        # Non-requester cannot rate (403)
        assert (
            await async_client.post(
                f"/api/v1/tickets/{tkt_id}/rate",
                headers=owner_headers,
                json={"satisfaction_rating": 1},
            )
        ).status_code == 403

        # Reopen within window -> succeeds and resets to open
        r_reopen = await async_client.post(
            f"/api/v1/tickets/{tkt_id}/reopen",
            headers=emp_headers,
            json={"reason": "Replacement chair squeaks loudly.", "start_workflow": False},
        )
        assert r_reopen.status_code == 200, r_reopen.text
        assert r_reopen.json()["status"] == "open"
        assert r_reopen.json()["resolved_at"] is None
        assert r_reopen.json()["closed_at"] is None

        # Resolve -> Close again preserving resolved_at & reopen_until, then expire reopen_until
        r_res2 = await async_client.patch(
            f"/api/v1/tickets/{tkt_id}",
            headers=owner_headers,
            json={"status": "resolved"},
        )
        assert r_res2.status_code == 200
        r_cls2 = await async_client.patch(
            f"/api/v1/tickets/{tkt_id}",
            headers=owner_headers,
            json={"status": "closed"},
        )
        assert r_cls2.status_code == 200
        assert r_cls2.json()["resolved_at"] == r_res2.json()["resolved_at"]
        assert r_cls2.json()["reopen_until"] == r_res2.json()["reopen_until"]

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.tickets SET reopen_until = $1 WHERE id = $2::uuid;",
                datetime.now(UTC) - timedelta(days=1),
                tkt_id,
            )
        finally:
            await conn.close()

        r_expired = await async_client.post(
            f"/api/v1/tickets/{tkt_id}/reopen",
            headers=emp_headers,
            json={"reason": "Too late to reopen", "start_workflow": False},
        )
        assert r_expired.status_code == 409
        assert "TICKET_REOPEN_WINDOW_EXPIRED" in r_expired.text
    finally:
        await _cleanup_globex_stage7(globex.tenant_id)


# ==============================================================================
# 4. Helpdesk Agent (`POST /api/v1/helpdesk/ask`): KB Citations & Guardrails
# ==============================================================================
@pytest.mark.asyncio
async def test_04_helpdesk_agent_kb_citations_and_privileged_or_unknown_tickets(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _cleanup_globex_stage7(globex.tenant_id)
    policy_ids: list[str] = []
    approval_ids: list[str] = []
    storage_keys: list[str] = []

    suffix = uuid.uuid4().hex[:6]
    try:
        # 1. Publish public helpdesk_kb VPN guide
        kb_doc_id, kb_app_id, kb_key = await _upload_and_publish_doc(
            async_client,
            owner_headers=owner_headers,
            title=f"IT VPN Setup Guide {suffix}",
            doc_type="helpdesk_knowledge",
            visibility="all",
            content=(
                b"# Corporate VPN Setup\n\n"
                b"Download WireGuard from the internal portal and import your corporate profile.\n"
                b"Select gateway vpn.globex.example and authenticate with Okta SSO.\n"
            ),
        )
        policy_ids.append(kb_doc_id)
        approval_ids.append(kb_app_id)
        storage_keys.append(kb_key)

        # 2. Publish hr_only helpdesk_kb runbook -> must NEVER leak to employee
        hr_doc_id, hr_app_id, hr_key = await _upload_and_publish_doc(
            async_client,
            owner_headers=owner_headers,
            title=f"Admin Root Runbook {suffix}",
            doc_type="helpdesk_knowledge",
            visibility="hr_only",
            content=(
                b"# Confidential HR Admin Root Key\n\n"
                b"Root break-glass token: SECRET_ROOT_TOKEN_XYZ987.\n"
            ),
        )
        policy_ids.append(hr_doc_id)
        approval_ids.append(hr_app_id)
        storage_keys.append(hr_key)

        # 3. Employee asks answerable KB question -> answered=True with verified citation
        r_kb = await async_client.post(
            "/api/v1/helpdesk/ask",
            headers=emp_headers,
            json={
                "question": "How do I setup corporate WireGuard VPN?",
                "start_workflow": False,
            },
        )
        assert r_kb.status_code == 200, r_kb.text
        d_kb = r_kb.json()
        assert d_kb["answered"] is True
        assert d_kb["ticket"] is None
        assert len(d_kb["citations"]) >= 1
        assert d_kb["citations"][0]["document_id"] == kb_doc_id
        assert d_kb["privileged_request_blocked"] is False

        # 4. Employee queries confidential hr_only runbook -> NEVER leaks secret token
        r_secret = await async_client.post(
            "/api/v1/helpdesk/ask",
            headers=emp_headers,
            json={
                "question": "What is the break-glass SECRET_ROOT_TOKEN_XYZ987?",
                "start_workflow": False,
            },
        )
        assert r_secret.status_code == 200, r_secret.text
        d_secret = r_secret.json()
        assert "SECRET_ROOT_TOKEN_XYZ987" not in d_secret["answer"]
        assert all(c["document_id"] != hr_doc_id for c in d_secret["citations"])

        # 5. Unanswerable facilities question -> answered=False + auto-created facilities ticket
        r_unk = await async_client.post(
            "/api/v1/helpdesk/ask",
            headers=emp_headers,
            json={
                "question": "The air conditioner in meeting room 3 is leaking water onto my desk.",
                "start_workflow": False,
            },
        )
        assert r_unk.status_code == 200, r_unk.text
        d_unk = r_unk.json()
        assert d_unk["answered"] is False
        assert d_unk["privileged_request_blocked"] is False
        assert d_unk["category"] == "facilities"
        assert d_unk["ticket"] is not None
        assert d_unk["ticket"]["source"] == "helpdesk_agent"

        # 6. Privileged action requests -> NEVER executed, auto-create routed tickets
        for q, expected_cat in (
            ("Please reset my coworker's password right now", "it_access"),
            ("Approve my annual leave request immediately", "leave_attendance"),
            ("Make me admin in this workspace", "it_access"),
        ):
            r_priv = await async_client.post(
                "/api/v1/helpdesk/ask",
                headers=emp_headers,
                json={"question": q, "start_workflow": False},
            )
            assert r_priv.status_code == 200, r_priv.text
            d_priv = r_priv.json()
            assert d_priv["answered"] is False
            assert d_priv["privileged_request_blocked"] is True
            assert d_priv["category"] == expected_cat
            assert d_priv["priority"] == "high"
            assert d_priv["ticket"]["source"] == "helpdesk_agent"
    finally:
        await _cleanup_globex_stage7(
            globex.tenant_id,
            policy_ids=policy_ids,
            approval_ids=approval_ids,
            storage_keys=storage_keys,
        )


# ==============================================================================
# 5. Temporal `TicketSLAWorkflow` (Pre-Breach Reminder + Breach Escalation)
# ==============================================================================
@pytest.mark.asyncio
async def test_05_temporal_ticket_sla_workflow_reminder_and_escalation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    await _cleanup_globex_stage7(globex.tenant_id)
    await _seed_globex_google_connector(globex.tenant_id)

    try:
        env = await WorkflowEnvironment.start_time_skipping()
        set_temporal_client(env.client)
        try:
            async with (
                env,
                Worker(
                    env.client,
                    task_queue="agentic-hr-task-queue",
                    workflows=[TicketSLAWorkflow],
                    activities=[
                        send_ticket_sla_reminder_activity,
                        escalate_ticket_sla_breach_activity,
                    ],
                ),
            ):
                # A. Unresolved ticket hits reminder timer -> breach escalation
                r1 = await async_client.post(
                    "/api/v1/tickets",
                    headers=emp_headers,
                    json={
                        "category": "facilities",
                        "priority": "high",
                        "subject": "Broken badge reader at main gate",
                        "description": "Front door badge reader offline.",
                        "start_workflow": True,
                        "reminder_delay_seconds": 0.05,
                        "breach_delay_seconds": 0.10,
                    },
                )
                assert r1.status_code == 201, r1.text
                t1_id = r1.json()["id"]

                h1 = env.client.get_workflow_handle(f"ticket-sla-{t1_id}")
                wf1_res = await h1.result()
                assert wf1_res["status"] == "escalated"

                # B. Ticket resolved early signals `ticket_resolved` -> exits cleanly without breach
                r2 = await async_client.post(
                    "/api/v1/tickets",
                    headers=emp_headers,
                    json={
                        "category": "general",
                        "priority": "low",
                        "subject": "Update locker number",
                        "description": "Need updated locker assignment.",
                        "start_workflow": True,
                        "reminder_delay_seconds": 3600.0,
                        "breach_delay_seconds": 7200.0,
                    },
                )
                assert r2.status_code == 201, r2.text
                t2_id = r2.json()["id"]

                r2_patch = await async_client.patch(
                    f"/api/v1/tickets/{t2_id}",
                    headers=owner_headers,
                    json={"status": "resolved"},
                )
                assert r2_patch.status_code == 200

                h2 = env.client.get_workflow_handle(f"ticket-sla-{t2_id}")
                wf2_res = await h2.result()
                assert wf2_res["status"] == "resolved"
        finally:
            set_temporal_client(None)

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            row1 = await conn.fetchrow(
                "SELECT sla_reminder_sent_at, sla_escalated_at, assigned_to_user_id FROM public.tickets WHERE id = $1::uuid;",
                t1_id,
            )
            assert row1["sla_reminder_sent_at"] is not None
            assert row1["sla_escalated_at"] is not None
            assert str(row1["assigned_to_user_id"]) == globex.owner.user_id

            breach_outbox = await conn.fetchval(
                "SELECT count(*) FROM public.outbox_events WHERE tenant_id = $1::uuid AND event_type = 'ticket.sla_breached';",
                globex.tenant_id,
            )
            assert breach_outbox == 1

            sla_emails = await conn.fetch(
                "SELECT template_name FROM public.sent_messages WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
            templates = {r["template_name"] for r in sla_emails}
            assert "ticket_sla_reminder" in templates
            assert "ticket_sla_escalated" in templates

            row2 = await conn.fetchrow(
                "SELECT sla_reminder_sent_at, sla_escalated_at FROM public.tickets WHERE id = $1::uuid;",
                t2_id,
            )
            assert row2["sla_reminder_sent_at"] is None
            assert row2["sla_escalated_at"] is None
        finally:
            await conn.close()
    finally:
        await _cleanup_globex_stage7(globex.tenant_id)
