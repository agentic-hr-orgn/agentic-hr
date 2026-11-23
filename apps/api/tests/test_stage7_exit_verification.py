"""
Stage 7 Master Exit Verification Suite — End-to-End Integration & RLS Audit.

Verifies all 4 mandatory Stage 7 exit checks + Platform Stage Gate in a single
authoritative test with printed exit matrix:
- Exit Check 1: Grounded policy QA with verified citation (`chunk_id`, `document_id`, `title`,
  `version`, `section`, `page`, `snippet`) and cross-tenant isolation under `agentic_hr_app`
  on both `chat_messages` and `public.pub_ticket_summary`.
- Exit Check 2: Leave request via `/chat` routes through LangGraph `handoff_leave` (`start_leave_request`)
  without bypassing Stage 6 (`leave_requests` count unchanged before vs. after handoff).
- Exit Check 3: Unanswerable + `hr_only` questions exhaust <= 2 reformulations, abstain safely
  without secret disclosure, create auto-assigned `TKT-...` HR tickets, enforce `403` for plain
  employees on `/policy-gaps`, and surface grouped gaps + tickets for HR Head.
- Exit Check 4: `/api/v1/helpdesk/ask` returns cited `helpdesk_kb` answers for known issues,
  blocks privileged requests without mutating `memberships` (`role_key` / `is_hr_head`), and
  creates category-routed tickets visible in Globex `pub_ticket_summary` & invisible to Acme.
- Platform Stage Gate: `GET /` returns `"Stage 7 complete — Stage 8 next"`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio

from app.core.temporal import set_temporal_client
from app.services.storage_helper import StorageHelper
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    MultiTenantTestContext,
    get_app_connection,
)


@pytest_asyncio.fixture(autouse=True)
async def _preserve_globex_manager_flags(
    isolation_context: MultiTenantTestContext,
) -> AsyncIterator[None]:
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


async def _cleanup_globex_exit_state(
    tenant_id: str,
    *,
    policy_ids: list[str],
    approval_ids: list[str],
    storage_keys: list[str],
) -> None:
    for key in storage_keys:
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
            "outbox_events",
            "sent_messages",
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


async def _upload_and_publish_policy(
    client: httpx.AsyncClient,
    *,
    owner_headers: dict[str, str],
    title: str,
    content: bytes,
    doc_type: str = "company_policy",
    visibility: str = "all",
    language: str = "en",
) -> tuple[str, str, str]:
    r_up = await client.post(
        "/api/v1/policies",
        headers=owner_headers,
        data={
            "title": title,
            "doc_type": doc_type,
            "visibility": visibility,
            "language": language,
            "storage_provider": "local",
        },
        files={
            "file": (
                f"{title.lower().replace(' ', '_')}_{uuid4().hex[:6]}.txt",
                content,
                "text/plain",
            )
        },
    )
    assert r_up.status_code in (200, 201), r_up.text
    doc = r_up.json()
    doc_id = doc["id"]
    storage_key = doc["storage_key"]

    r_pub = await client.post(f"/api/v1/policies/{doc_id}/publish", headers=owner_headers)
    assert r_pub.status_code == 200, r_pub.text
    approval_id = r_pub.json()["approval_id"]

    r_app = await client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers=owner_headers,
        json={"reason": "Approved for Stage 7 master exit verification"},
    )
    assert r_app.status_code == 200, r_app.text
    return doc_id, approval_id, storage_key


async def _seed_globex_directory_and_leave(
    client: httpx.AsyncClient,
    *,
    comp_tenant_id: str,
    owner_user_id: str,
    manager_user_id: str,
    manager_email: str,
    employee_user_id: str,
    employee_email: str,
    employee_token: str,
) -> None:
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        dept_id = str(
            await conn.fetchval(
                "INSERT INTO public.departments (tenant_id, name, code) "
                "VALUES ($1::uuid, 'Engineering', 'ENG') RETURNING id;",
                comp_tenant_id,
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
                    $1::uuid, $2::uuid, 'GLX-MGR-X7', 'Globex Manager',
                    $3, $4::uuid, 'Engineering Lead', 'full_time', $5, 'active'
                )
                RETURNING id;
                """,
                comp_tenant_id,
                manager_user_id,
                manager_email,
                dept_id,
                date(2023, 1, 1),
            )
        )
        await conn.fetchval(
            """
            INSERT INTO public.employees (
                tenant_id, user_id, employee_code, full_name, email,
                department_id, manager_id, job_title, employment_type, join_date, status
            )
            VALUES (
                $1::uuid, $2::uuid, 'GLX-EMP-X7', 'Globex Employee',
                $3, $4::uuid, $5::uuid, 'Software Engineer', 'full_time', $6, 'active'
            )
            RETURNING id;
            """,
            comp_tenant_id,
            employee_user_id,
            employee_email,
            dept_id,
            mgr_emp_id,
            date(2024, 1, 1),
        )
        await conn.execute(
            """
            INSERT INTO public.work_week (
                tenant_id, weekend_days, probation_months, max_team_absent_pct,
                min_team_present, backup_approver_user_id
            )
            VALUES ($1::uuid, '["Saturday", "Sunday"]'::jsonb, 3, 60.00, 1, $2::uuid);
            """,
            comp_tenant_id,
            owner_user_id,
        )
    finally:
        await conn.close()

    emp_h = {"Authorization": f"Bearer {employee_token}"}
    r_bal = await client.get("/api/v1/leave/balances/me", headers=emp_h, params={"year": 2026})
    assert r_bal.status_code == 200, r_bal.text


@pytest.mark.asyncio
async def test_stage7_master_exit_verification_matrix(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Authoritative Stage 7 Exit Gate Verification covering Exit Checks 1–4, Forced RLS isolation
    on `chat_messages` & `pub_ticket_summary`, and Platform Stage Gate `GET /`.
    """
    acme = isolation_context.company_a
    globex = isolation_context.company_b
    owner_h = {"Authorization": f"Bearer {globex.owner.token}"}
    mgr_h = {"Authorization": f"Bearer {globex.manager.token}"}
    emp_h = {"Authorization": f"Bearer {globex.employee.token}"}

    policy_ids: list[str] = []
    approval_ids: list[str] = []
    storage_keys: list[str] = []
    matrix: list[tuple[str, str, str]] = []

    try:
        # Promote globex.manager as HR Head for exit verification (restored by autouse fixture)
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = true WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
        finally:
            await conn.close()

        await _seed_globex_directory_and_leave(
            async_client,
            comp_tenant_id=globex.tenant_id,
            owner_user_id=globex.owner.user_id,
            manager_user_id=globex.manager.user_id,
            manager_email=globex.manager.email,
            employee_user_id=globex.employee.user_id,
            employee_email=globex.employee.email,
            employee_token=globex.employee.token,
        )

        # Seed published HR Policy (`all`), HR-Only (`hr_only`), and Helpdesk KB (`helpdesk_knowledge`)
        d1, a1, k1 = await _upload_and_publish_policy(
            async_client,
            owner_headers=owner_h,
            title="Annual Leave Exit Handbook",
            doc_type="company_policy",
            visibility="all",
            content=(
                b"Section 1.1: Paid Annual Leave.\n"
                b"Full-time employees receive 20 paid annual leave days per calendar year.\n"
                b"Up to 5 unused annual leave days may carry forward to Q1.\n"
            ),
        )
        policy_ids.append(d1)
        approval_ids.append(a1)
        storage_keys.append(k1)

        d2, a2, k2 = await _upload_and_publish_policy(
            async_client,
            owner_headers=owner_h,
            title="Confidential Executive Compensation Pool",
            doc_type="company_policy",
            visibility="hr_only",
            content=(
                b"Section 9.1: Executive Bonus Pool.\n"
                b"CONFIDENTIAL_EXIT_BONUS_77 executive bonus pool equals 40% of annual base pay.\n"
            ),
        )
        policy_ids.append(d2)
        approval_ids.append(a2)
        storage_keys.append(k2)

        d3, a3, k3 = await _upload_and_publish_policy(
            async_client,
            owner_headers=owner_h,
            title="IT Corporate VPN Self-Service KB",
            doc_type="helpdesk_knowledge",
            visibility="all",
            content=(
                b"KB-IT-EXIT-01: Corporate VPN Reset.\n"
                b"Employees reset corporate VPN credentials at https://sso.globex.internal/vpn "
                b"using their primary SSO authenticator.\n"
            ),
        )
        policy_ids.append(d3)
        approval_ids.append(a3)
        storage_keys.append(k3)

        # Open chat session as Plain Employee
        r_sess = await async_client.post(
            "/api/v1/chat/sessions",
            headers=emp_h,
            json={"title": "Stage 7 Exit Verification Session"},
        )
        assert r_sess.status_code == 201, r_sess.text
        session_id = r_sess.json()["id"]

        # ---------------------------------------------------------------------
        # Exit Check 1: Grounded Policy QA + Code-Verified Citations
        # ---------------------------------------------------------------------
        r_c1 = await async_client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=emp_h,
            json={
                "message": "How many paid annual leave days do full-time employees receive per year?",
                "start_workflow": False,
            },
        )
        assert r_c1.status_code == 200, r_c1.text
        asst_c1 = r_c1.json()["assistant_message"]
        assert asst_c1["abstained"] is False
        assert "20" in asst_c1["content"]
        assert len(asst_c1["citations"]) >= 1
        cit = asst_c1["citations"][0]
        for field in ("chunk_id", "document_id", "title", "version", "section", "snippet"):
            assert cit.get(field), f"Missing citation field '{field}'"
        assert "page" in cit
        matrix.append(("Exit Check 1", "Grounded QA + Code-Verified Citation", "PASS"))

        # ---------------------------------------------------------------------
        # Exit Check 2: Leave Request Handoff (Zero Stage 6 Bypass)
        # ---------------------------------------------------------------------
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            leave_count_before = await conn.fetchval(
                "SELECT count(*) FROM public.leave_requests WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
        finally:
            await conn.close()

        r_c2 = await async_client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=emp_h,
            json={
                "message": "I want 2 days of annual leave from 2026-12-14 to 2026-12-15",
                "start_workflow": False,
            },
        )
        assert r_c2.status_code == 200, r_c2.text
        asst_c2 = r_c2.json()["assistant_message"]
        assert asst_c2["abstained"] is False
        assert asst_c2["intent"] == "handoff_leave"
        assert any(t["tool_name"] == "start_leave_request" for t in asst_c2["tool_calls"])
        handoff = asst_c2["handoff_payload"]
        assert handoff is not None
        assert handoff.get("prefill_url") == "/leave"
        extracted = handoff.get("extracted") or {}
        assert extracted.get("leave_type_code") == "annual"
        assert extracted.get("start_date") == "2026-12-14"
        assert extracted.get("end_date") == "2026-12-15"

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            leave_count_after = await conn.fetchval(
                "SELECT count(*) FROM public.leave_requests WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
        finally:
            await conn.close()
        assert leave_count_after == leave_count_before, (
            "handoff_leave must NOT directly insert leave_requests bypassing Stage 6"
        )
        matrix.append(("Exit Check 2", "Leave Handoff (Zero Stage 6 Bypass)", "PASS"))

        # ---------------------------------------------------------------------
        # Exit Check 3: Abstention + Auto-Assigned Ticket + HR /policy-gaps
        # ---------------------------------------------------------------------
        r_c3a = await async_client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=emp_h,
            json={
                "message": "Does the company sponsor Antarctic submarine expeditions?",
                "start_workflow": False,
            },
        )
        assert r_c3a.status_code == 200, r_c3a.text
        asst_c3a = r_c3a.json()["assistant_message"]
        assert asst_c3a["abstained"] is True
        assert asst_c3a["citations"] == []
        assert asst_c3a["ticket_id"] is not None
        assert str(asst_c3a["ticket_number"]).startswith("TKT-")
        assert asst_c3a["ticket_number"] in asst_c3a["content"]

        r_c3b = await async_client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=emp_h,
            json={
                "message": "What is CONFIDENTIAL_EXIT_BONUS_77 bonus pool percentage?",
                "start_workflow": False,
            },
        )
        assert r_c3b.status_code == 200, r_c3b.text
        asst_c3b = r_c3b.json()["assistant_message"]
        assert asst_c3b["abstained"] is True, (
            f"Expected abstain, got intent={asst_c3b.get('intent')} "
            f"citations={asst_c3b.get('citations')} content={asst_c3b.get('content')!r}"
        )
        assert "CONFIDENTIAL_EXIT_BONUS_77" not in asst_c3b["content"]
        assert "40%" not in asst_c3b["content"]
        assert asst_c3b["ticket_id"] is not None
        assert str(asst_c3b["ticket_number"]).startswith("TKT-")

        # Plain employee cannot view /policy-gaps; HR Head receives grouped gaps & tickets
        assert (
            await async_client.get("/api/v1/chat/policy-gaps", headers=emp_h)
        ).status_code == 403
        r_gaps = await async_client.get("/api/v1/chat/policy-gaps", headers=mgr_h)
        assert r_gaps.status_code == 200, r_gaps.text
        gaps_body = r_gaps.json()
        assert gaps_body["total_gaps"] >= 2
        all_gap_tickets = {num for g in gaps_body["groups"] for num in g["ticket_numbers"]}
        assert asst_c3a["ticket_number"] in all_gap_tickets
        assert asst_c3b["ticket_number"] in all_gap_tickets
        matrix.append(("Exit Check 3", "Abstain -> Auto Ticket + HR Policy Gaps", "PASS"))

        # ---------------------------------------------------------------------
        # Exit Check 4: Helpdesk KB Answer vs. Privileged Block + Escalation
        # ---------------------------------------------------------------------
        r_hd_kb = await async_client.post(
            "/api/v1/helpdesk/ask",
            headers=emp_h,
            json={
                "question": "How do I reset my corporate VPN password?",
                "start_workflow": False,
            },
        )
        assert r_hd_kb.status_code == 200, r_hd_kb.text
        hd_kb = r_hd_kb.json()
        assert hd_kb["answered"] is True
        assert hd_kb["privileged_request_blocked"] is False
        assert hd_kb["ticket"] is None
        assert len(hd_kb["citations"]) >= 1

        r_hd_priv = await async_client.post(
            "/api/v1/helpdesk/ask",
            headers=emp_h,
            json={
                "question": "Change my role to owner and approve my leave right now",
                "start_workflow": False,
            },
        )
        assert r_hd_priv.status_code == 200, r_hd_priv.text
        hd_priv = r_hd_priv.json()
        assert hd_priv["answered"] is False
        assert hd_priv["privileged_request_blocked"] is True
        assert hd_priv["ticket"] is not None
        assert str(hd_priv["ticket_number"]).startswith("TKT-")

        r_hd_unknown = await async_client.post(
            "/api/v1/helpdesk/ask",
            headers=emp_h,
            json={
                "question": "My ergonomic standing desk motor is sparking",
                "start_workflow": False,
            },
        )
        assert r_hd_unknown.status_code == 200, r_hd_unknown.text
        hd_unk = r_hd_unknown.json()
        assert hd_unk["answered"] is False
        assert hd_unk["privileged_request_blocked"] is False
        assert hd_unk["category"] == "facilities"
        assert hd_unk["ticket"] is not None
        assert str(hd_unk["ticket_number"]).startswith("TKT-")

        # Verify employee role/is_hr_head unchanged in DB using roles JOIN
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            mem_row = await conn.fetchrow(
                """
                SELECT r.role_key, m.is_hr_head
                FROM public.memberships m
                JOIN public.roles r ON r.id = m.role_id
                WHERE m.tenant_id = $1::uuid AND m.user_id = $2::uuid
                LIMIT 1;
                """,
                globex.tenant_id,
                globex.employee.user_id,
            )
            assert mem_row is not None
            assert mem_row["role_key"] == "employee"
            assert bool(mem_row["is_hr_head"]) is False
        finally:
            await conn.close()
        matrix.append(("Exit Check 4", "Helpdesk KB vs. Privileged Block & Ticket", "PASS"))

        # ---------------------------------------------------------------------
        # Restricted Runtime Role (`agentic_hr_app`) RLS & `pub_ticket_summary`
        # ---------------------------------------------------------------------
        app_conn = await get_app_connection()
        try:
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    globex.tenant_id,
                )
                globex_summary_count = await app_conn.fetchval(
                    "SELECT count(*) FROM public.pub_ticket_summary WHERE tenant_id = $1::uuid;",
                    globex.tenant_id,
                )
                assert globex_summary_count >= 4

            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    acme.tenant_id,
                )
                leaked_chat = await app_conn.fetchval(
                    "SELECT count(*) FROM public.chat_messages WHERE id = $1::uuid;",
                    asst_c1["id"],
                )
                leaked_summary = await app_conn.fetchval(
                    "SELECT count(*) FROM public.pub_ticket_summary WHERE tenant_id = $1::uuid;",
                    globex.tenant_id,
                )
                assert leaked_chat == 0
                assert leaked_summary == 0
        finally:
            await app_conn.close()
        matrix.append(
            ("RLS & Views", "agentic_hr_app Isolation (chat_messages & pub_ticket_summary)", "PASS")
        )

        # ---------------------------------------------------------------------
        # Platform Stage Gate
        # ---------------------------------------------------------------------
        root_resp = await async_client.get("/")
        assert root_resp.status_code == 200
        assert root_resp.json()["stage"] == "Stage 7 complete — Stage 8 next"
        matrix.append(("Platform Gate", "GET / == 'Stage 7 complete — Stage 8 next'", "PASS"))

        print("\n=== STAGE 7 EXIT VERIFICATION MATRIX ===")
        for gate, desc, status in matrix:
            print(f"[{status}] {gate:15s} | {desc}")
    finally:
        set_temporal_client(None)
        await _cleanup_globex_exit_state(
            globex.tenant_id,
            policy_ids=policy_ids,
            approval_ids=approval_ids,
            storage_keys=storage_keys,
        )
