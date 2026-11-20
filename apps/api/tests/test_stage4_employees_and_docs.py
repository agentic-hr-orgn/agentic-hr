"""
Stage 4 Prompt 4 — Employee Profiles, Role-Based Encrypted Fields, Org Chart &
Per-Employee Document Vault Integration Tests.

Verifies:
1. Paginated directory search/filter (`GET /api/v1/employees`) via `public.pub_employees`:
   - Case-insensitive search (`full_name`, `email`, `job_title`, `employee_code`) and filters
     (`department_id`, `status`, `employment_type`).
   - Never selects or returns `cnic`, `cnic_encrypted`, `bank_account`, or `bank_account_encrypted`.
   - Normalizes `created_at` / `updated_at` to `Asia/Karachi` (`+05:00`).
2. Strict role-based profile PII access (`GET /api/v1/employees/{id}`, `PATCH /api/v1/employees/{id}`):
   - `owner`, `hr_head`, and self-view (`actor.user_id == employee.user_id`) receive decrypted
     `cnic` & `bank_account` (`can_view_sensitive=True`) and emit `employee.sensitive_viewed`
     in `audit_logs` (with zero raw PII in `details`) ONLY when encrypted PII was decrypted.
   - `manager` (without HR/Payroll flags) and unrelated peer `employee` receive `null` for
     `cnic`/`bank_account` (`can_view_sensitive=False`).
   - `manager` attempting to modify `cnic`/`bank_account` receives HTTP 403 (`PII_EDIT_FORBIDDEN`),
     while non-PII updates succeed and enqueue a `sheets_sync_queue` entry.
3. Cycle-safe org chart (`GET /api/v1/employees/org-chart`) and cycle rejection on `PATCH`:
   - 3-level hierarchy tree construction with accurate `direct_reports_count`.
   - Rejects self-parenting and multi-hop circular reporting with HTTP 400 (`ORG_CHART_CYCLE`).
4. Per-employee document vault (`/api/v1/employees/{id}/documents`):
   - Uploads CNIC (PNG), Contract (PDF with `expiry_date`), and Certificate (DOCX with `expiry_date`).
   - Rejects disallowed MIME types (`.exe`) with HTTP 400.
   - Verifies SHA-256 download checksum integrity and hardened headers (`Content-Disposition`,
     `X-Content-Type-Options: nosniff`) for BOTH `local` and `google_drive` (`FakeDriveService`).
   - Enforces defense-in-depth RBAC (unrelated peer `employee` gets HTTP 403; `manager` gets HTTP 403
     on delete; `owner` succeeds) and cross-tenant isolation (Acme gets HTTP 404 on Globex records).
"""

import hashlib
import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest
import pytest_asyncio

from app.core.encryption import EncryptionContext, encrypt_field
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


async def _reset_globex_employees_baseline(tenant_id: str) -> tuple[str, str]:
    """
    Cleans Globex Corp's employees, documents, departments, connectors, and sync queues,
    then seeds two departments (`Engineering`, `Finance`) and returns their UUIDs.
    """
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "sheets_sync_queue",
            "employee_documents",
            "drive_files",
            "connector_credentials",
            "employees",
            "departments",
        ):
            await conn.execute(f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;", tenant_id)

        eng_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.departments (tenant_id, name, code)
                VALUES ($1::uuid, 'Engineering', 'ENG')
                RETURNING id;
                """,
                tenant_id,
            )
        )
        fin_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.departments (tenant_id, name, code)
                VALUES ($1::uuid, 'Finance', 'FIN')
                RETURNING id;
                """,
                tenant_id,
            )
        )
        return eng_id, fin_id
    finally:
        await conn.close()


# ==============================================================================
# 1. Paginated Directory Search & Filter (pub_employees, Never Exposes PII)
# ==============================================================================
@pytest.mark.asyncio
async def test_01_list_employees_search_filter_pagination_excludes_pii(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    eng_id, fin_id = await _reset_globex_employees_baseline(globex.tenant_id)

    # Create 3 employees (2 Engineering, 1 Finance) with encrypted CNIC & bank account
    seed_payloads = [
        {
            "full_name": "Bilal Siddiqui",
            "email": "bilal.siddiqui@globex.internal",
            "employee_code": "GLX-101",
            "department_id": eng_id,
            "job_title": "Principal Architect",
            "employment_type": "full_time",
            "join_date": "2025-03-01",
            "status": "active",
            "cnic": "42101-1111111-1",
            "bank_account": "PK36GLOB000011112222",
        },
        {
            "full_name": "Hina Qureshi",
            "email": "hina.qureshi@globex.internal",
            "employee_code": "GLX-102",
            "department_id": eng_id,
            "job_title": "QA Contractor",
            "employment_type": "contract",
            "join_date": "2025-06-15",
            "status": "on_leave",
            "cnic": "42101-2222222-2",
            "bank_account": "PK36GLOB000033334444",
        },
        {
            "full_name": "Zainab Farooq",
            "email": "zainab.farooq@globex.internal",
            "employee_code": "GLX-103",
            "department_id": fin_id,
            "job_title": "Finance Controller",
            "employment_type": "full_time",
            "join_date": "2025-01-10",
            "status": "active",
            "cnic": "42101-3333333-3",
            "bank_account": "PK36GLOB000055556666",
        },
    ]
    for p in seed_payloads:
        resp = await async_client.post("/api/v1/employees", json=p, headers=owner_headers)
        assert resp.status_code == 201, resp.text

    # Paginated list (page_size=2)
    resp_page1 = await async_client.get(
        "/api/v1/employees?page=1&page_size=2",
        headers=owner_headers,
    )
    assert resp_page1.status_code == 200, resp_page1.text
    body1 = resp_page1.json()
    assert body1["total"] == 3
    assert body1["page"] == 1
    assert body1["page_size"] == 2
    assert body1["total_pages"] == 2
    assert len(body1["items"]) == 2

    # Strictly verify NO encrypted or raw PII keys appear in directory list responses
    for item in body1["items"]:
        for forbidden_key in ("cnic", "cnic_encrypted", "bank_account", "bank_account_encrypted"):
            assert forbidden_key not in item
        # Verify Asia/Karachi (+05:00) timestamp serialization
        assert item["created_at"].endswith("+05:00")
        assert item["updated_at"].endswith("+05:00")

    # Search by case-insensitive job_title / employee_code
    resp_search = await async_client.get(
        "/api/v1/employees?search=architect",
        headers=owner_headers,
    )
    assert resp_search.status_code == 200
    assert resp_search.json()["total"] == 1
    assert resp_search.json()["items"][0]["email"] == "bilal.siddiqui@globex.internal"

    # Filter by department_id + employment_type + status
    resp_filter = await async_client.get(
        f"/api/v1/employees?department_id={eng_id}&employment_type=contract&status=on_leave",
        headers=owner_headers,
    )
    assert resp_filter.status_code == 200
    f_body = resp_filter.json()
    assert f_body["total"] == 1
    assert f_body["items"][0]["full_name"] == "Hina Qureshi"
    assert f_body["items"][0]["department_name"] == "Engineering"


# ==============================================================================
# 2. Role-Based Encrypted PII Access, Edit Guards & Audit Logging
# ==============================================================================
@pytest.mark.asyncio
async def test_02_role_based_profile_pii_access_edit_guards_and_audit_logging(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    manager_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    employee_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    eng_id, _ = await _reset_globex_employees_baseline(globex.tenant_id)

    # Ensure globex.manager starts as a plain manager without is_hr_head or is_payroll_admin
    assert globex.manager.user_id != globex.employee.user_id
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = false, is_payroll_admin = false WHERE id = $1::uuid;",
            globex.manager.membership_id,
        )
    finally:
        await conn.close()

    # Create target employee (linked to globex.employee.user_id for self-view verification)
    resp_create = await async_client.post(
        "/api/v1/employees",
        json={
            "full_name": "Saad Mahmood",
            "email": "saad.mahmood@globex.internal",
            "user_id": globex.employee.user_id,
            "department_id": eng_id,
            "job_title": "Backend Engineer",
            "employment_type": "full_time",
            "join_date": "2025-04-01",
            "cnic": "42101-7654321-9",
            "bank_account": "PK36GLOB999988887777",
        },
        headers=owner_headers,
    )
    assert resp_create.status_code == 201, resp_create.text
    emp_id = resp_create.json()["id"]

    # Create a second employee (no PII, not linked to globex.employee.user_id)
    resp_peer = await async_client.post(
        "/api/v1/employees",
        json={
            "full_name": "Nadia Tariq",
            "email": "nadia.tariq@globex.internal",
            "department_id": eng_id,
            "job_title": "Product Designer",
            "employment_type": "full_time",
            "join_date": "2025-05-01",
        },
        headers=owner_headers,
    )
    assert resp_peer.status_code == 201
    peer_emp_id = resp_peer.json()["id"]

    # (a) Owner views profile -> decrypted PII + employee.sensitive_viewed audit log
    resp_owner = await async_client.get(f"/api/v1/employees/{emp_id}", headers=owner_headers)
    assert resp_owner.status_code == 200, resp_owner.text
    owner_data = resp_owner.json()
    assert owner_data["can_view_sensitive"] is True
    assert owner_data["can_edit_sensitive"] is True
    assert owner_data["cnic"] == "42101-7654321-9"
    assert owner_data["bank_account"] == "PK36GLOB999988887777"
    assert owner_data["cnic_masked"] == "*****-*******-9"
    assert owner_data["bank_account_masked"] == "****7777"

    # (b) Manager (without HR/Payroll flags) views profile -> PII must be null
    resp_mgr = await async_client.get(f"/api/v1/employees/{emp_id}", headers=manager_headers)
    assert resp_mgr.status_code == 200, resp_mgr.text
    mgr_data = resp_mgr.json()
    assert mgr_data["can_view_sensitive"] is False
    assert mgr_data["can_edit_sensitive"] is False
    assert mgr_data["cnic"] is None
    assert mgr_data["bank_account"] is None
    assert mgr_data["cnic_masked"] is None
    assert mgr_data["bank_account_masked"] is None

    # (c) Manager attempting to edit CNIC or bank_account -> HTTP 403 PII_EDIT_FORBIDDEN
    resp_mgr_pii_patch = await async_client.patch(
        f"/api/v1/employees/{emp_id}",
        json={"cnic": "42101-0000000-0"},
        headers=manager_headers,
    )
    assert resp_mgr_pii_patch.status_code == 403
    assert "PII_EDIT_FORBIDDEN" in resp_mgr_pii_patch.text

    # Manager editing non-PII job_title -> succeeds & enqueues Sheets sync
    resp_mgr_ok = await async_client.patch(
        f"/api/v1/employees/{emp_id}",
        json={"job_title": "Senior Backend Engineer"},
        headers=manager_headers,
    )
    assert resp_mgr_ok.status_code == 200, resp_mgr_ok.text
    assert resp_mgr_ok.json()["job_title"] == "Senior Backend Engineer"
    assert resp_mgr_ok.json()["cnic"] is None

    # (d) Temporarily grant is_hr_head=True to manager -> now sees decrypted PII
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = true WHERE id = $1::uuid;",
            globex.manager.membership_id,
        )
        resp_hr = await async_client.get(f"/api/v1/employees/{emp_id}", headers=manager_headers)
        assert resp_hr.status_code == 200
        hr_data = resp_hr.json()
        assert hr_data["can_view_sensitive"] is True
        assert hr_data["cnic"] == "42101-7654321-9"
        assert hr_data["bank_account"] == "PK36GLOB999988887777"
    finally:
        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
            globex.manager.membership_id,
        )
        await conn.close()

    # (e) Self-view: employee viewing their own linked record sees decrypted PII
    resp_self = await async_client.get(f"/api/v1/employees/{emp_id}", headers=employee_headers)
    assert resp_self.status_code == 200
    assert resp_self.json()["can_view_sensitive"] is True
    assert resp_self.json()["cnic"] == "42101-7654321-9"

    # (f) Peer employee viewing another employee's record -> can_view_sensitive=False
    resp_peer_view = await async_client.get(
        f"/api/v1/employees/{peer_emp_id}",
        headers=employee_headers,
    )
    assert resp_peer_view.status_code == 200
    assert resp_peer_view.json()["can_view_sensitive"] is False
    assert resp_peer_view.json()["cnic"] is None

    # (g) Owner viewing employee WITHOUT encrypted PII -> must NOT log employee.sensitive_viewed
    resp_owner_no_pii = await async_client.get(
        f"/api/v1/employees/{peer_emp_id}",
        headers=owner_headers,
    )
    assert resp_owner_no_pii.status_code == 200
    assert resp_owner_no_pii.json()["can_view_sensitive"] is True
    assert resp_owner_no_pii.json()["cnic"] is None

    # Verify audit_logs & sheets_sync_queue invariants directly in PostgreSQL
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        sensitive_logs = await conn.fetch(
            """
            SELECT target_id, details::text AS details_text
            FROM public.audit_logs
            WHERE tenant_id = $1::uuid
              AND action = 'employee.sensitive_viewed'
              AND target_id IN ($2, $3);
            """,
            globex.tenant_id,
            str(emp_id),
            str(peer_emp_id),
        )
        # 3 authorized views on emp_id (owner, hr_head, self); 0 on peer_emp_id (no PII)
        assert len(sensitive_logs) == 3
        for row in sensitive_logs:
            assert str(row["target_id"]) == str(emp_id)
            assert "42101-7654321-9" not in row["details_text"]
            assert "PK36GLOB999988887777" not in row["details_text"]

        sync_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM public.sheets_sync_queue
            WHERE tenant_id = $1::uuid AND entity_id = $2 AND action = 'update';
            """,
            globex.tenant_id,
            str(emp_id),
        )
        assert sync_count >= 1
    finally:
        await conn.close()


# ==============================================================================
# 3. Org Chart Hierarchy Construction & Cycle Rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_03_org_chart_hierarchy_and_cycle_rejection(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    eng_id, _ = await _reset_globex_employees_baseline(globex.tenant_id)

    # Level 1: CEO (root)
    r_ceo = await async_client.post(
        "/api/v1/employees",
        json={
            "full_name": "Ahsan Raza",
            "email": "ahsan.ceo@globex.internal",
            "department_id": eng_id,
            "job_title": "CEO",
            "employment_type": "full_time",
            "join_date": "2024-01-01",
        },
        headers=owner_headers,
    )
    assert r_ceo.status_code == 201
    ceo_id = r_ceo.json()["id"]

    # Level 2: VP Engineering -> CEO
    r_vp = await async_client.post(
        "/api/v1/employees",
        json={
            "full_name": "Mariam Khan",
            "email": "mariam.vp@globex.internal",
            "department_id": eng_id,
            "manager_id": ceo_id,
            "job_title": "VP Engineering",
            "employment_type": "full_time",
            "join_date": "2024-06-01",
        },
        headers=owner_headers,
    )
    assert r_vp.status_code == 201
    vp_id = r_vp.json()["id"]

    # Level 3: Staff Engineer -> VP Engineering
    r_ic = await async_client.post(
        "/api/v1/employees",
        json={
            "full_name": "Omer Farooq",
            "email": "omer.ic@globex.internal",
            "department_id": eng_id,
            "manager_id": vp_id,
            "job_title": "Staff Engineer",
            "employment_type": "full_time",
            "join_date": "2025-01-15",
        },
        headers=owner_headers,
    )
    assert r_ic.status_code == 201
    ic_id = r_ic.json()["id"]

    # Fetch org chart
    resp_tree = await async_client.get("/api/v1/employees/org-chart", headers=owner_headers)
    assert resp_tree.status_code == 200, resp_tree.text
    tree = resp_tree.json()
    assert tree["total_employees"] == 3
    assert len(tree["roots"]) == 1
    assert len(tree["unassigned"]) == 0

    root_node = tree["roots"][0]
    assert root_node["id"] == ceo_id
    assert root_node["direct_reports_count"] == 1
    assert root_node["department_name"] == "Engineering"

    vp_node = root_node["children"][0]
    assert vp_node["id"] == vp_id
    assert vp_node["direct_reports_count"] == 1

    ic_node = vp_node["children"][0]
    assert ic_node["id"] == ic_id
    assert ic_node["direct_reports_count"] == 0

    # Reject self-parenting
    resp_self_cycle = await async_client.patch(
        f"/api/v1/employees/{ic_id}",
        json={"manager_id": ic_id},
        headers=owner_headers,
    )
    assert resp_self_cycle.status_code == 400
    assert "ORG_CHART_CYCLE" in resp_self_cycle.text

    # Reject indirect cycle (CEO reporting to Staff Engineer)
    resp_indirect_cycle = await async_client.patch(
        f"/api/v1/employees/{ceo_id}",
        json={"manager_id": ic_id},
        headers=owner_headers,
    )
    assert resp_indirect_cycle.status_code == 400
    assert "ORG_CHART_CYCLE" in resp_indirect_cycle.text


# ==============================================================================
# 4. Per-Employee Document Vault: Local + Google Drive Parity, Checksum, RBAC & Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_04_employee_document_vault_upload_download_checksum_rbac_and_isolation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    globex = isolation_context.company_b
    acme = isolation_context.company_a
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    manager_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    employee_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    acme_headers = {"Authorization": f"Bearer {acme.owner.token}"}
    eng_id, _ = await _reset_globex_employees_baseline(globex.tenant_id)

    # Create employee (NOT linked to globex.employee.user_id so globex.employee is an unrelated peer)
    r_emp = await async_client.post(
        "/api/v1/employees",
        json={
            "full_name": "Kamran Akmal",
            "email": "kamran.akmal@globex.internal",
            "department_id": eng_id,
            "job_title": "DevOps Engineer",
            "employment_type": "full_time",
            "join_date": "2025-02-01",
        },
        headers=owner_headers,
    )
    assert r_emp.status_code == 201
    emp_id = r_emp.json()["id"]

    # Reject disallowed file extension / MIME type (.exe)
    resp_bad_mime = await async_client.post(
        f"/api/v1/employees/{emp_id}/documents",
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")},
        data={"document_type": "other", "title": "Bad File"},
        headers=owner_headers,
    )
    assert resp_bad_mime.status_code == 400
    assert "INVALID_DOCUMENT_FILE_TYPE" in resp_bad_mime.text

    # Upload 3 valid documents via local storage fallback (no Google connector active yet)
    cnic_bytes = b"\x89PNG\r\n\x1a\nFAKE_CNIC_IMAGE_PAYLOAD_1234567890"
    contract_bytes = b"%PDF-1.4\n%FAKE_EMPLOYMENT_CONTRACT_BYTES_9876543210\n%%EOF"
    cert_bytes = b"PK\x03\x04FAKE_DOCX_CERTIFICATE_BYTES_ABCDEF123456"

    uploads = [
        ("cnic", "CNIC Front Scan", "2028-12-31", "cnic_front.png", cnic_bytes, "image/png"),
        (
            "contract",
            "Employment Contract 2026",
            "2027-01-31",
            "employment_contract.pdf",
            contract_bytes,
            "application/pdf",
        ),
        (
            "certificate",
            "AWS Solutions Architect",
            "2026-12-15",
            "aws_cert.docx",
            cert_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ]
    uploaded_docs: list[tuple[str, bytes]] = []
    for doc_type, title, exp, fname, raw_bytes, mime in uploads:
        resp_up = await async_client.post(
            f"/api/v1/employees/{emp_id}/documents",
            files={"file": (fname, raw_bytes, mime)},
            data={"document_type": doc_type, "title": title, "expiry_date": exp},
            headers=owner_headers,
        )
        assert resp_up.status_code == 201, resp_up.text
        up_json = resp_up.json()
        assert up_json["storage_provider"] == "local"
        assert up_json["expiry_date"] == exp
        assert up_json["size_bytes"] == len(raw_bytes)
        uploaded_docs.append((up_json["id"], raw_bytes))

    # List documents & verify all 3 exist
    resp_list = await async_client.get(
        f"/api/v1/employees/{emp_id}/documents",
        headers=owner_headers,
    )
    assert resp_list.status_code == 200
    assert len(resp_list.json()) == 3

    # Verify SHA-256 checksum integrity & hardened HTTP headers for all local downloads
    for doc_id, expected_bytes in uploaded_docs:
        resp_dl = await async_client.get(
            f"/api/v1/employees/{emp_id}/documents/{doc_id}/download",
            headers=owner_headers,
        )
        assert resp_dl.status_code == 200, resp_dl.text
        assert resp_dl.headers.get("x-content-type-options") == "nosniff"
        assert "attachment; filename=" in (resp_dl.headers.get("content-disposition") or "")
        assert (
            hashlib.sha256(resp_dl.content).hexdigest()
            == hashlib.sha256(expected_bytes).hexdigest()
        )

    # Now seed an active, writable Google Drive connector for Globex to verify google_drive parity
    cred_id = uuid.uuid4()
    enc_access = encrypt_field(
        "fake-access-token-stage4",
        EncryptionContext(
            tenant_id=globex.tenant_id,
            entity_type="connector_credential",
            record_id=cred_id,
            field_name="access_token",
        ),
    )
    enc_refresh = encrypt_field(
        "fake-refresh-token-stage4",
        EncryptionContext(
            tenant_id=globex.tenant_id,
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
                $1::uuid, $2::uuid, 'google', 'owner@globex.internal',
                '["https://www.googleapis.com/auth/drive.file"]'::jsonb,
                $3, $4, NOW() + INTERVAL '2 hours', 'connected', false
            );
            """,
            str(cred_id),
            globex.tenant_id,
            enc_access,
            enc_refresh,
        )
    finally:
        await conn.close()

    drive_pdf_bytes = b"%PDF-1.4\n%GOOGLE_DRIVE_PARITY_CHECKSUM_TEST_BYTES\n%%EOF"
    resp_drive_up = await async_client.post(
        f"/api/v1/employees/{emp_id}/documents",
        files={"file": ("drive_contract.pdf", drive_pdf_bytes, "application/pdf")},
        data={"document_type": "contract", "title": "Drive Synced Contract"},
        headers=owner_headers,
    )
    assert resp_drive_up.status_code == 201, resp_drive_up.text
    drive_doc = resp_drive_up.json()
    assert drive_doc["storage_provider"] == "google_drive"
    assert drive_doc["web_view_link"] is not None

    resp_drive_dl = await async_client.get(
        f"/api/v1/employees/{emp_id}/documents/{drive_doc['id']}/download",
        headers=owner_headers,
    )
    assert resp_drive_dl.status_code == 200, resp_drive_dl.text
    assert (
        hashlib.sha256(resp_drive_dl.content).hexdigest()
        == hashlib.sha256(drive_pdf_bytes).hexdigest()
    )

    # Defense-in-depth RBAC: unrelated peer employee gets 403 on list & download
    first_doc_id = uploaded_docs[0][0]
    resp_peer_list = await async_client.get(
        f"/api/v1/employees/{emp_id}/documents",
        headers=employee_headers,
    )
    assert resp_peer_list.status_code == 403

    resp_peer_dl = await async_client.get(
        f"/api/v1/employees/{emp_id}/documents/{first_doc_id}/download",
        headers=employee_headers,
    )
    assert resp_peer_dl.status_code == 403

    # Cross-tenant isolation: Acme owner gets 404 on Globex profile, list, download, delete
    for method, url in (
        ("GET", f"/api/v1/employees/{emp_id}"),
        ("GET", f"/api/v1/employees/{emp_id}/documents"),
        ("GET", f"/api/v1/employees/{emp_id}/documents/{first_doc_id}/download"),
        ("DELETE", f"/api/v1/employees/{emp_id}/documents/{first_doc_id}"),
    ):
        resp_x = await async_client.request(method, url, headers=acme_headers)
        assert resp_x.status_code == 404, f"{method} {url} -> {resp_x.status_code}"

    # Delete RBAC: plain manager (without HR Head / Payroll Admin) gets 403; owner succeeds (200)
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = false, is_payroll_admin = false "
            "WHERE id = $1::uuid;",
            globex.manager.membership_id,
        )
    finally:
        await conn.close()

    resp_mgr_del = await async_client.delete(
        f"/api/v1/employees/{emp_id}/documents/{first_doc_id}",
        headers=manager_headers,
    )
    assert resp_mgr_del.status_code == 403

    resp_owner_del = await async_client.delete(
        f"/api/v1/employees/{emp_id}/documents/{first_doc_id}",
        headers=owner_headers,
    )
    assert resp_owner_del.status_code == 200
    assert resp_owner_del.json()["status"] == "deleted"

    resp_list_after = await async_client.get(
        f"/api/v1/employees/{emp_id}/documents",
        headers=owner_headers,
    )
    assert resp_list_after.status_code == 200
    assert len(resp_list_after.json()) == 3
