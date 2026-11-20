"""
Stage 4 Prompt 8 — Final Milestone B Exit Verification, Cross-Tenant API Isolation Matrix,
and Live `agentic_hr_app` RLS / Secure View Audit.

Verifies:
1. Full 10-employee Milestone B End-to-End Walkthrough on isolated Globex Corp tenant:
   - Connect Google Workspace credential + save Onboarding Steps 1..3 (Stage 6 defaults).
   - Upload policy document stub (`ingestion_status="queued_stub"`, outbox `policy.uploaded.stub`).
   - Create departments + run two-phase Excel bulk import of 10 employees (3-level internal
     `manager_email` hierarchy + encrypted Pakistani CNIC & IBAN).
   - Verify `GET /api/v1/employees` excludes PII, `GET /api/v1/employees/org-chart` renders
     all 10 employees in a 3-level hierarchy (`roots`, `unassigned`), and `GET /api/v1/onboarding/readiness`
     reports `can_go_live = True`.
   - Execute `POST /api/v1/onboarding/launch` with `Idempotency-Key` -> `is_live = True`,
     `+05:00` PKT `launched_at`, `sheets_sync_queue`, invitations, and deterministic zero-duplicate replay.
   - Upload 3 employee documents expiring at +30/+7/+1 days relative to `date(2030, 6, 1)` PKT,
     run expiry scan twice -> first creates `info`/`warning`/`critical` alerts + emails, second creates 0.
   - Verify RBAC PII boundary (Owner/HR Head/Self vs plain Manager/Peer Employee) & `audit_logs`
     hygiene (zero raw CNIC/IBAN in `details::text`).
2. Cross-Tenant API Isolation Matrix (Tenant A Acme vs Tenant B Globex) across all 10 Stage 4 routes.
3. Live `agentic_hr_app` bogus-tenant (`00000000-0000-0000-0000-000000000000`) RLS & Secure View audit
   across all 6 Stage 4 tables + both `security_invoker`/`security_barrier` views.
"""

import uuid
from datetime import date
from io import BytesIO

import asyncpg
import httpx
import openpyxl
import pytest

from app.core.encryption import EncryptionContext, encrypt_field
from tests.isolation_harness import (
    APP_DB_URL,
    MIGRATION_DB_URL,
    MultiTenantTestContext,
)


def _build_10_employee_xlsx(tenant_domain: str = "globex.internal") -> bytes:
    """Builds a valid 10-employee Excel workbook with a 3-level manager hierarchy and Pakistani PII."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Employees"

    headers = [
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
    ws.append(headers)

    rows = [
        # Level 1 (Root)
        [
            "Tariq Siddiqui",
            f"tariq.ceo@{tenant_domain}",
            "+92-300-1000001",
            "Executive",
            "",
            "Chief Executive Officer",
            "full_time",
            "2023-01-10",
            "42101-1000001-1",
            "PK36HABB0000100000010001",
        ],
        # Level 2 (3 Department Leads reporting to CEO)
        [
            "Sana Mirza",
            f"sana.eng@{tenant_domain}",
            "+92-300-1000002",
            "Engineering",
            f"tariq.ceo@{tenant_domain}",
            "VP Engineering",
            "full_time",
            "2023-02-15",
            "42101-1000002-2",
            "PK36HABB0000100000010002",
        ],
        [
            "Bilal Qureshi",
            f"bilal.ops@{tenant_domain}",
            "+92-300-1000003",
            "Operations",
            f"tariq.ceo@{tenant_domain}",
            "Head of Operations",
            "full_time",
            "2023-03-01",
            "42101-1000003-3",
            "PK36HABB0000100000010003",
        ],
        [
            "Hina Farooq",
            f"hina.hr@{tenant_domain}",
            "+92-300-1000004",
            "HR",
            f"tariq.ceo@{tenant_domain}",
            "HR Lead",
            "full_time",
            "2023-04-01",
            "42101-1000004-4",
            "PK36HABB0000100000010004",
        ],
        # Level 3 (6 Staff reporting to Leads)
        [
            "Umar Hayat",
            f"umar.dev1@{tenant_domain}",
            "+92-300-1000005",
            "Engineering",
            f"sana.eng@{tenant_domain}",
            "Senior Backend Engineer",
            "full_time",
            "2024-01-05",
            "42101-1000005-5",
            "PK36HABB0000100000010005",
        ],
        [
            "Ayesha Khan",
            f"ayesha.dev2@{tenant_domain}",
            "+92-300-1000006",
            "Engineering",
            f"sana.eng@{tenant_domain}",
            "Frontend Engineer",
            "full_time",
            "2024-02-10",
            "42101-1000006-6",
            "PK36HABB0000100000010006",
        ],
        [
            "Zain Raza",
            f"zain.qa@{tenant_domain}",
            "+92-300-1000007",
            "Engineering",
            f"sana.eng@{tenant_domain}",
            "QA Automation Engineer",
            "contract",
            "2024-03-12",
            "42101-1000007-7",
            "PK36HABB0000100000010007",
        ],
        [
            "Nadia Javed",
            f"nadia.ops1@{tenant_domain}",
            "+92-300-1000008",
            "Operations",
            f"bilal.ops@{tenant_domain}",
            "Operations Specialist",
            "full_time",
            "2024-04-15",
            "42101-1000008-8",
            "PK36HABB0000100000010008",
        ],
        [
            "Hamza Malik",
            f"hamza.ops2@{tenant_domain}",
            "+92-300-1000009",
            "Operations",
            f"bilal.ops@{tenant_domain}",
            "Logistics Coordinator",
            "part_time",
            "2024-05-20",
            "42101-1000009-9",
            "PK36HABB0000100000010009",
        ],
        [
            "Maham Tariq",
            f"maham.hr1@{tenant_domain}",
            "+92-300-1000010",
            "HR",
            f"hina.hr@{tenant_domain}",
            "People Operations Associate",
            "intern",
            "2024-06-01",
            "42101-1000010-0",
            "PK36HABB0000100000010010",
        ],
    ]

    for row in rows:
        ws.append(row)
        for col_idx in range(1, len(row) + 1):
            cell = ws.cell(row=ws.max_row, column=col_idx)
            cell.value = str(row[col_idx - 1])
            cell.data_type = "s"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_invalid_single_row_xlsx() -> bytes:
    """Builds a minimal 1-row invalid .xlsx workbook so Acme can test error-report isolation."""
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
    bad_row = [
        "Bad Row",
        "not-an-email",
        "",
        "NonExistentDept",
        "",
        "Tester",
        "full_time",
        "2024-01-01",
        "invalid-cnic",
        "BAD",
    ]
    ws.append(bad_row)
    for col_idx, val in enumerate(bad_row, start=1):
        cell = ws.cell(row=ws.max_row, column=col_idx)
        cell.value = val
        cell.data_type = "s"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _reset_tenant_stage4_state(tenant_id: str) -> None:
    """Resets Stage 4 tables & connector state for a clean isolated test baseline."""
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "notifications",
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


async def _seed_google_credential(tenant_id: str, account_email: str) -> str:
    """Seeds an active, writable Google Workspace credential with valid future expiry."""
    cred_id = uuid.uuid4()
    enc_access = encrypt_field(
        "fake-exit-access-token",
        EncryptionContext(
            tenant_id=tenant_id,
            entity_type="connector_credential",
            record_id=cred_id,
            field_name="access_token",
        ),
    )
    enc_refresh = encrypt_field(
        "fake-exit-refresh-token",
        EncryptionContext(
            tenant_id=tenant_id,
            entity_type="connector_credential",
            record_id=cred_id,
            field_name="refresh_token",
        ),
    )
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            """
            INSERT INTO public.connector_credentials (
                id, tenant_id, provider, account_email, scopes,
                encrypted_access_token, encrypted_refresh_token,
                token_expiry, status, is_read_only
            )
            VALUES (
                $1::uuid, $2::uuid, 'google', $3,
                '["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/calendar.events"]'::jsonb,
                $4, $5, NOW() + INTERVAL '2 hours', 'connected', false
            );
            """,
            str(cred_id),
            tenant_id,
            account_email,
            enc_access,
            enc_refresh,
        )
    finally:
        await conn.close()
    return str(cred_id)


# ==============================================================================
# 1. Full 10-Employee Milestone B End-to-End Exit Walkthrough
# ==============================================================================
@pytest.mark.asyncio
async def test_01_full_milestone_b_10_employee_exit_walkthrough(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Executes the complete Milestone B (Stage 4) exit walkthrough on Globex Corp:
    Google credential + Steps 1..3 -> policy stub -> 4 departments + 10-employee 3-level
    Excel import -> directory/org-chart/readiness -> idempotent PKT launch -> 30/7/1-day
    document expiry scan & dedupe -> RBAC PII boundary & audit log hygiene.
    """
    globex = isolation_context.company_b
    await _reset_tenant_stage4_state(globex.tenant_id)
    await _seed_google_credential(globex.tenant_id, "owner@globex.internal")

    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    manager_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    # Ensure globex.manager is a plain manager (not HR Head / Payroll Admin) for RBAC PII test
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    orig_mgr_flags = await conn.fetchrow(
        "SELECT is_hr_head, is_payroll_admin FROM public.memberships WHERE id = $1::uuid;",
        globex.manager.membership_id,
    )
    await conn.execute(
        "UPDATE public.memberships SET is_hr_head = false, is_payroll_admin = false "
        "WHERE id = $1::uuid;",
        globex.manager.membership_id,
    )
    await conn.close()

    try:
        # 1. Save Onboarding Steps 1..3 & verify Stage 6 defaults
        for step_num, payload in [
            (1, {"advance_step": True}),
            (2, {"advance_step": True}),
            (3, {"business_type": "Software & Technology", "advance_step": True}),
        ]:
            resp_step = await async_client.put(
                f"/api/v1/onboarding/steps/{step_num}",
                json=payload,
                headers=owner_headers,
            )
            assert resp_step.status_code == 200, resp_step.text
        step3_data = resp_step.json()
        assert step3_data["current_step"] == 4
        assert step3_data["structure_config"]["weekends"] == ["Saturday", "Sunday"]

        # 2. Upload a Policy Document stub (`queued_stub` + outbox event)
        resp_policy = await async_client.post(
            "/api/v1/policies",
            data={"title": "Globex Leave & Attendance Policy", "language": "en"},
            files={
                "file": (
                    "globex_policy.pdf",
                    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF",
                    "application/pdf",
                )
            },
            headers=owner_headers,
        )
        assert resp_policy.status_code == 201, resp_policy.text
        assert resp_policy.json()["ingestion_status"] == "queued_stub"

        # 3. Create 4 departments + two-phase Excel bulk import of 10 employees
        for name, code in [
            ("Executive", "EXE"),
            ("Engineering", "ENG"),
            ("Operations", "OPS"),
            ("HR", "HR"),
        ]:
            resp_dept = await async_client.post(
                "/api/v1/departments",
                json={"name": name, "code": code},
                headers=owner_headers,
            )
            assert resp_dept.status_code == 201, resp_dept.text

        xlsx_bytes = _build_10_employee_xlsx("globex.internal")
        resp_val = await async_client.post(
            "/api/v1/employees/import/validate",
            files={
                "file": (
                    "globex_10_employees.xlsx",
                    xlsx_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=owner_headers,
        )
        assert resp_val.status_code == 200, resp_val.text
        val_data = resp_val.json()
        assert val_data["total_rows"] == 10
        assert val_data["valid_count"] == 10
        assert val_data["invalid_count"] == 0
        assert val_data["can_commit"] is True
        batch_id = val_data["import_batch_id"]

        resp_commit = await async_client.post(
            f"/api/v1/employees/import/{batch_id}/commit",
            headers=owner_headers,
        )
        assert resp_commit.status_code == 200, resp_commit.text
        commit_data = resp_commit.json()
        assert commit_data["committed_count"] == 10
        assert len(commit_data["employee_ids"]) == 10

        # 4. Directory PII exclusion, 3-level Org Chart, and Readiness Check
        resp_dir = await async_client.get(
            "/api/v1/employees?page=1&page_size=25",
            headers=owner_headers,
        )
        assert resp_dir.status_code == 200, resp_dir.text
        dir_data = resp_dir.json()
        assert dir_data["total"] == 10
        for item in dir_data["items"]:
            assert "cnic" not in item
            assert "bank_account" not in item
            assert "encrypted_cnic" not in item
            assert "encrypted_bank_account" not in item

        resp_chart = await async_client.get("/api/v1/employees/org-chart", headers=owner_headers)
        assert resp_chart.status_code == 200, resp_chart.text
        chart_data = resp_chart.json()
        assert chart_data["total_employees"] == 10
        assert chart_data["unassigned"] == []
        assert len(chart_data["roots"]) == 1
        ceo_node = chart_data["roots"][0]
        assert ceo_node["email"] == "tariq.ceo@globex.internal"
        assert ceo_node["direct_reports_count"] == 3
        total_level3 = sum(len(lead["children"]) for lead in ceo_node["children"])
        assert total_level3 == 6

        resp_ready = await async_client.get("/api/v1/onboarding/readiness", headers=owner_headers)
        assert resp_ready.status_code == 200, resp_ready.text
        assert resp_ready.json()["can_go_live"] is True

        # 5. Workspace Launch with Idempotency-Key + Replay Check
        launch_key = f"exit-launch-{uuid.uuid4()}"
        resp_launch1 = await async_client.post(
            "/api/v1/onboarding/launch",
            headers={**owner_headers, "Idempotency-Key": launch_key},
        )
        assert resp_launch1.status_code == 200, resp_launch1.text
        launch_data1 = resp_launch1.json()
        assert launch_data1["is_live"] is True
        assert launch_data1["launched_at"].endswith("+05:00")
        assert launch_data1["invitations_sent"] == 10
        assert launch_data1["sheets_queued"] == 10

        resp_launch2 = await async_client.post(
            "/api/v1/onboarding/launch",
            headers={**owner_headers, "Idempotency-Key": launch_key},
        )
        assert resp_launch2.status_code == 200, resp_launch2.text
        assert resp_launch2.json()["launched_at"] == launch_data1["launched_at"]

        # Verify DB outbox & zero duplicate sent_messages/sheets_sync_queue after replay
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            policy_outbox = await conn.fetchval(
                "SELECT COUNT(*) FROM public.outbox_events "
                "WHERE tenant_id = $1::uuid AND event_type = 'policy.uploaded.stub';",
                globex.tenant_id,
            )
            assert policy_outbox >= 1
            sent_count = await conn.fetchval(
                "SELECT COUNT(*) FROM public.sent_messages WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
            assert sent_count == 10
        finally:
            await conn.close()

        # 6. Upload 3 expiring documents (+30d, +7d, +1d from 2030-06-01 PKT) & verify expiry scan
        ref_date = date(2030, 6, 1)
        target_emp = next(i for i in dir_data["items"] if i["email"] == "umar.dev1@globex.internal")
        target_emp_id = target_emp["id"]
        peer_emp = next(i for i in dir_data["items"] if i["email"] == "ayesha.dev2@globex.internal")
        peer_emp_id = peer_emp["id"]

        # Link target_emp to globex.employee user_id so Self PII check is tested too
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.employees SET user_id = $1::uuid WHERE id = $2::uuid;",
                globex.employee.user_id,
                target_emp_id,
            )
        finally:
            await conn.close()

        for days_ahead, doc_title in [
            (30, "CNIC Renewal 30d"),
            (7, "Work Permit 7d"),
            (1, "Contract Renewal 1d"),
        ]:
            exp = ref_date.fromordinal(ref_date.toordinal() + days_ahead)
            resp_doc = await async_client.post(
                f"/api/v1/employees/{target_emp_id}/documents",
                data={
                    "document_type": "cnic" if days_ahead == 30 else "contract",
                    "title": doc_title,
                    "expiry_date": exp.isoformat(),
                },
                files={"file": (f"{days_ahead}d.pdf", b"%PDF-1.4\ndoc\n%%EOF", "application/pdf")},
                headers=owner_headers,
            )
            assert resp_doc.status_code == 201, resp_doc.text

        resp_scan1 = await async_client.post(
            "/api/v1/notifications/trigger-expiry-scan",
            json={"reference_date": ref_date.isoformat()},
            headers=owner_headers,
        )
        assert resp_scan1.status_code == 200, resp_scan1.text
        scan1 = resp_scan1.json()
        assert scan1["documents_matched"] == 3
        # 3 docs x 2 recipients (Globex Owner + linked employee) = 6 alerts & 6 emails
        assert scan1["alerts_created"] == 6
        assert scan1["emails_sent"] == 6

        # Second scan on the same day must be 100% deduped
        resp_scan2 = await async_client.post(
            "/api/v1/notifications/trigger-expiry-scan",
            json={"reference_date": ref_date.isoformat()},
            headers=owner_headers,
        )
        assert resp_scan2.status_code == 200, resp_scan2.text
        scan2 = resp_scan2.json()
        assert scan2["alerts_created"] == 0
        assert scan2["emails_sent"] == 0

        resp_notifs = await async_client.get("/api/v1/notifications", headers=owner_headers)
        assert resp_notifs.status_code == 200
        assert resp_notifs.json()["total"] == 6
        severities = {n["severity"] for n in resp_notifs.json()["items"]}
        assert {"info", "warning", "critical"}.issubset(severities)

        # 7. RBAC PII Boundary & Audit Log Hygiene
        for auth_hdr in (owner_headers, emp_headers):
            r_ok = await async_client.get(f"/api/v1/employees/{target_emp_id}", headers=auth_hdr)
            assert r_ok.status_code == 200
            d_ok = r_ok.json()
            assert d_ok["can_view_sensitive"] is True
            assert d_ok["cnic"] == "42101-1000005-5"
            assert d_ok["bank_account"] == "PK36HABB0000100000010005"

        # Plain Manager viewing another employee -> redacted
        r_mgr = await async_client.get(
            f"/api/v1/employees/{target_emp_id}", headers=manager_headers
        )
        assert r_mgr.status_code == 200
        assert r_mgr.json()["can_view_sensitive"] is False
        assert r_mgr.json()["cnic"] is None
        assert r_mgr.json()["bank_account"] is None

        # Promote manager to HR Head temporarily -> now sees decrypted PII
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = true WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
            r_hr = await async_client.get(
                f"/api/v1/employees/{target_emp_id}", headers=manager_headers
            )
            assert r_hr.status_code == 200
            d_hr = r_hr.json()
            assert d_hr["can_view_sensitive"] is True
            assert d_hr["cnic"] == "42101-1000005-5"
            assert d_hr["bank_account"] == "PK36HABB0000100000010005"
        finally:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
            await conn.close()

        # Peer Employee viewing another employee -> redacted
        r_peer = await async_client.get(f"/api/v1/employees/{peer_emp_id}", headers=emp_headers)
        assert r_peer.status_code == 200
        assert r_peer.json()["can_view_sensitive"] is False
        assert r_peer.json()["cnic"] is None
        assert r_peer.json()["bank_account"] is None

        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            logs = await conn.fetch(
                """
                SELECT target_id, details::text AS details_text
                FROM public.audit_logs
                WHERE tenant_id = $1::uuid
                  AND action = 'employee.sensitive_viewed'
                  AND target_id = $2;
                """,
                globex.tenant_id,
                str(target_emp_id),
            )
            assert len(logs) == 3
            for row in logs:
                assert "42101-1000005-5" not in row["details_text"]
                assert "PK36HABB0000100000010005" not in row["details_text"]
        finally:
            await conn.close()
    finally:
        if orig_mgr_flags is not None:
            conn = await asyncpg.connect(MIGRATION_DB_URL)
            try:
                await conn.execute(
                    "UPDATE public.memberships SET is_hr_head = $1, is_payroll_admin = $2 "
                    "WHERE id = $3::uuid;",
                    orig_mgr_flags["is_hr_head"],
                    orig_mgr_flags["is_payroll_admin"],
                    globex.manager.membership_id,
                )
            finally:
                await conn.close()


# ==============================================================================
# 2. Cross-Tenant API Isolation Matrix (Tenant A Acme vs Tenant B Globex)
# ==============================================================================
@pytest.mark.asyncio
async def test_02_cross_tenant_api_isolation_matrix(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies Tenant B (Globex) cannot read, mutate, download, or commit any Tenant A (Acme)
    Stage 4 resource across all 10 Stage 4 route families.
    """
    acme = isolation_context.company_a
    globex = isolation_context.company_b
    await _reset_tenant_stage4_state(globex.tenant_id)

    acme_headers = {"Authorization": f"Bearer {acme.owner.token}"}
    globex_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    acme_dept_id: str | None = None
    acme_policy_id: str | None = None
    acme_emp_id: str | None = None
    acme_doc_id: str | None = None
    acme_notif_id: str | None = None

    try:
        # Create fresh Acme resources for each route family
        resp_dept = await async_client.post(
            "/api/v1/departments",
            json={
                "name": f"Acme Iso Dept {uuid.uuid4().hex[:6]}",
                "code": f"ISO{uuid.uuid4().hex[:4]}",
            },
            headers=acme_headers,
        )
        assert resp_dept.status_code == 201, resp_dept.text
        acme_dept_id = resp_dept.json()["id"]

        resp_policy = await async_client.post(
            "/api/v1/policies",
            data={"title": "Acme Isolation Policy", "language": "en"},
            files={"file": ("acme_iso.pdf", b"%PDF-1.4\nisolation\n%%EOF", "application/pdf")},
            headers=acme_headers,
        )
        assert resp_policy.status_code == 201, resp_policy.text
        acme_policy_id = resp_policy.json()["id"]

        resp_emp = await async_client.post(
            "/api/v1/employees",
            json={
                "full_name": "Acme Isolated Staff",
                "email": f"iso.{uuid.uuid4().hex[:6]}@acme.internal",
                "department_id": acme_dept_id,
                "job_title": "Security Reviewer",
                "employment_type": "full_time",
                "join_date": "2025-01-15",
                "cnic": "42101-5555555-5",
                "bank_account": "PK36ACME0000555555555555",
            },
            headers=acme_headers,
        )
        assert resp_emp.status_code == 201, resp_emp.text
        acme_emp_id = resp_emp.json()["id"]

        resp_doc = await async_client.post(
            f"/api/v1/employees/{acme_emp_id}/documents",
            data={"document_type": "other", "title": "Acme Secret Doc"},
            files={"file": ("secret.pdf", b"%PDF-1.4\nsecret\n%%EOF", "application/pdf")},
            headers=acme_headers,
        )
        assert resp_doc.status_code == 201, resp_doc.text
        acme_doc_id = resp_doc.json()["id"]

        # Acme invalid batch for error-report isolation
        resp_bad_val = await async_client.post(
            "/api/v1/employees/import/validate",
            files={
                "file": (
                    "bad.xlsx",
                    _build_invalid_single_row_xlsx(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=acme_headers,
        )
        assert resp_bad_val.status_code == 200, resp_bad_val.text
        acme_bad_batch_id = resp_bad_val.json()["import_batch_id"]

        # Insert one notification directly for Acme
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            acme_notif_id = str(
                await conn.fetchval(
                    """
                    INSERT INTO public.notifications (
                        tenant_id, recipient_user_id, category, severity,
                        title, message, dedupe_key
                    )
                    VALUES (
                        $1::uuid, $2::uuid, 'document_expiry', 'info',
                        'Acme Private Alert', 'Internal Acme Alert', $3
                    )
                    RETURNING id;
                    """,
                    acme.tenant_id,
                    acme.owner.user_id,
                    f"acme-iso-{uuid.uuid4()}",
                )
            )
        finally:
            await conn.close()

        # 1. Onboarding state & 2. Readiness scoped strictly to Globex
        r_state = await async_client.get("/api/v1/onboarding/state", headers=globex_headers)
        assert r_state.status_code == 200
        assert r_state.json()["tenant_id"] == globex.tenant_id

        r_ready = await async_client.get("/api/v1/onboarding/readiness", headers=globex_headers)
        assert r_ready.status_code == 200
        assert r_ready.json()["counts"]["employees"] == 0
        assert r_ready.json()["counts"]["policies"] == 0

        # 3. Departments PATCH & DELETE -> 404
        assert (
            await async_client.patch(
                f"/api/v1/departments/{acme_dept_id}",
                json={"name": "Hijacked"},
                headers=globex_headers,
            )
        ).status_code == 404
        assert (
            await async_client.delete(f"/api/v1/departments/{acme_dept_id}", headers=globex_headers)
        ).status_code == 404

        # 4. Policies DELETE -> 404 & GET /policies omits Acme policy
        assert (
            await async_client.delete(f"/api/v1/policies/{acme_policy_id}", headers=globex_headers)
        ).status_code == 404
        r_pol_list = await async_client.get("/api/v1/policies", headers=globex_headers)
        assert r_pol_list.status_code == 200
        assert all(p["id"] != acme_policy_id for p in r_pol_list.json())

        # 5. Employees GET / PATCH -> 404
        assert (
            await async_client.get(f"/api/v1/employees/{acme_emp_id}", headers=globex_headers)
        ).status_code == 404
        assert (
            await async_client.patch(
                f"/api/v1/employees/{acme_emp_id}",
                json={"job_title": "Hijacked"},
                headers=globex_headers,
            )
        ).status_code == 404

        # 6. Employee Documents List -> 404
        assert (
            await async_client.get(
                f"/api/v1/employees/{acme_emp_id}/documents", headers=globex_headers
            )
        ).status_code == 404

        # 7. Employee Document Download -> 404
        assert (
            await async_client.get(
                f"/api/v1/employees/{acme_emp_id}/documents/{acme_doc_id}/download",
                headers=globex_headers,
            )
        ).status_code == 404

        # 8. Import Error Report -> 404
        assert (
            await async_client.get(
                f"/api/v1/employees/import/{acme_bad_batch_id}/error-report",
                headers=globex_headers,
            )
        ).status_code == 404

        # 9. Import Commit -> 404
        assert (
            await async_client.post(
                f"/api/v1/employees/import/{acme_bad_batch_id}/commit",
                headers=globex_headers,
            )
        ).status_code == 404

        # 10. Notifications Mark Read -> 404
        assert (
            await async_client.patch(
                f"/api/v1/notifications/{acme_notif_id}/read",
                headers=globex_headers,
            )
        ).status_code == 404
    finally:
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            if acme_notif_id:
                await conn.execute(
                    "DELETE FROM public.notifications WHERE id = $1::uuid;",
                    acme_notif_id,
                )
            if acme_doc_id:
                await conn.execute(
                    "DELETE FROM public.employee_documents WHERE id = $1::uuid;",
                    acme_doc_id,
                )
            if acme_emp_id:
                await conn.execute(
                    "DELETE FROM public.sheets_sync_queue "
                    "WHERE tenant_id = $1::uuid AND entity_id = $2;",
                    acme.tenant_id,
                    acme_emp_id,
                )
                await conn.execute(
                    "DELETE FROM public.employees WHERE id = $1::uuid;",
                    acme_emp_id,
                )
            if acme_policy_id:
                await conn.execute(
                    "DELETE FROM public.policy_documents WHERE id = $1::uuid;",
                    acme_policy_id,
                )
            if acme_dept_id:
                await conn.execute(
                    "DELETE FROM public.departments WHERE id = $1::uuid;",
                    acme_dept_id,
                )
        finally:
            await conn.close()


# ==============================================================================
# 3. Live `agentic_hr_app` Bogus-Tenant RLS & Secure View Audit
# ==============================================================================
@pytest.mark.asyncio
async def test_03_live_rls_and_secure_views_bogus_tenant_audit(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Connects directly as non-superuser `agentic_hr_app` with bogus tenant UUID
    `00000000-0000-0000-0000-000000000000` and verifies 0 rows across all 6 Stage 4
    tables and both `security_invoker`/`security_barrier` views, plus FORCE RLS flags.
    """
    bogus_tenant = "00000000-0000-0000-0000-000000000000"
    stage4_tables = (
        "departments",
        "employees",
        "employee_documents",
        "policy_documents",
        "onboarding_state",
        "notifications",
    )
    stage4_views = (
        "pub_employees",
        "pub_documents_expiring",
    )

    conn = await asyncpg.connect(APP_DB_URL)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true);", bogus_tenant)
            for tbl in stage4_tables:
                cnt = await conn.fetchval(f"SELECT COUNT(*) FROM public.{tbl};")
                assert cnt == 0, f"RLS leak on {tbl}: expected 0 rows, got {cnt}"
            for view_name in stage4_views:
                cnt = await conn.fetchval(f"SELECT COUNT(*) FROM public.{view_name};")
                assert cnt == 0, f"Secure view leak on {view_name}: expected 0 rows, got {cnt}"

        # Verify FORCE ROW LEVEL SECURITY is enabled on every Stage 4 table
        for tbl in stage4_tables:
            flags = await conn.fetchrow(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = $1;
                """,
                tbl,
            )
            assert flags is not None, f"Missing table {tbl}"
            assert flags["relrowsecurity"] is True, f"{tbl} missing ENABLE ROW LEVEL SECURITY"
            assert flags["relforcerowsecurity"] is True, f"{tbl} missing FORCE ROW LEVEL SECURITY"
    finally:
        await conn.close()
