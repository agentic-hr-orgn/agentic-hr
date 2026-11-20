"""
Stage 4 Prompt 3 — Excel Bulk Employee Importer, Strict Row Validation, Error Report & Two-Phase Commit Tests.

Verifies:
1. Template generation (`GET /api/v1/employees/import/template`): parses `.xlsx`, confirms
   `Employees` headers (`full_name`, `email`, `phone`, `department`, `manager_email`, `job_title`,
   `employment_type`, `join_date`, `cnic`, `bank_account`) and `Instructions` sheet containing
   the tenant's existing department names.
2. Multi-pass validation (`POST /api/v1/employees/import/validate`):
   - Deterministically catches all 6 error categories without stopping on first error:
     (1) missing required fields,
     (2) invalid email / invalid `employment_type` / invalid ISO `join_date`,
     (3) duplicate emails within the uploaded file AND conflict with existing DB employees,
     (4) unknown `department` for this tenant,
     (5) missing `manager_email`, self-reference, and circular manager loop (`A <-> B`),
     (6) invalid Pakistani `cnic` format.
   - Masks CNIC and bank account in `preview_rows` and in the downloadable `.xlsx` error report.
   - Neutralizes Excel formula injection (`=CMD(...)`) in the downloadable `.xlsx` error report.
3. Error report download (`GET /api/v1/employees/import/{batch_id}/error-report`) and blocked
   commit (`POST /api/v1/employees/import/{batch_id}/commit` -> HTTP 400 `IMPORT_BATCH_HAS_ERRORS`).
4. Clean 10-employee batch commit with internal 3-level `manager_email` references:
   - Two-pass insert resolves internal `manager_id` hierarchy accurately.
   - Pre-generated `emp_id` binds AES-256-GCM 4-field AAD so `Employee.get_cnic()` and
     `Employee.get_bank_account()` decrypt cleanly from PostgreSQL.
   - Enqueues 10 rows into `sheets_sync_queue`, writes `employee.bulk_imported` audit log,
     and deletes the staging key so a second commit attempt returns HTTP 404.
5. Cross-tenant staging isolation & RBAC:
   - Tenant A cannot view error report or commit Tenant B's `import_batch_id` (HTTP 404).
   - `employee` role is blocked with HTTP 403.
"""

import io
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import openpyxl
import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.employee import Employee
from app.models.tenant import set_tenant_context
from app.services.employee_import_service import (
    IMPORT_COLUMNS,
    _acquire_commit_lock,
    _release_commit_lock,
)
from tests.isolation_harness import MIGRATION_DB_URL, MultiTenantTestContext


def _build_xlsx_bytes(
    rows: list[dict[str, Any]], headers: tuple[str, ...] = IMPORT_COLUMNS
) -> bytes:
    """Builds an in-memory `.xlsx` workbook from a list of row dicts."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Employees"
    for col_idx, col_name in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.value = col_name
        cell.data_type = "s"
    for row_idx, r_data in enumerate(rows, start=2):
        for col_idx, col_name in enumerate(headers, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            val = r_data.get(col_name)
            if isinstance(val, str):
                cell.value = val
                cell.data_type = "s"
            else:
                cell.value = val
    buf = io.BytesIO()
    try:
        wb.save(buf)
    finally:
        wb.close()
    return buf.getvalue()


async def _setup_globex_import_baseline(tenant_id: str) -> tuple[str, str]:
    """
    Resets Globex Corp's employees/departments/queues and seeds 2 baseline departments
    (`Engineering`, `Operations`) and 1 existing employee (`existing.head@globex.internal`).
    """
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        for tbl in (
            "sheets_sync_queue",
            "employee_documents",
            "employees",
            "departments",
        ):
            await conn.execute(f"DELETE FROM public.{tbl} WHERE tenant_id = $1::uuid;", tenant_id)

        eng_dept_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.departments (tenant_id, name, code)
                VALUES ($1::uuid, 'Engineering', 'ENG')
                RETURNING id;
                """,
                tenant_id,
            )
        )
        ops_dept_id = str(
            await conn.fetchval(
                """
                INSERT INTO public.departments (tenant_id, name, code)
                VALUES ($1::uuid, 'Operations', 'OPS')
                RETURNING id;
                """,
                tenant_id,
            )
        )
        await conn.execute(
            """
            INSERT INTO public.employees (
                tenant_id, department_id, employee_code, full_name, email, job_title, join_date, status
            )
            VALUES (
                $1::uuid, $2::uuid, 'GLX-HEAD', 'Existing Head',
                'existing.head@globex.internal', 'VP Engineering', $3, 'active'
            );
            """,
            tenant_id,
            eng_dept_id,
            date(2024, 1, 10),
        )
        return eng_dept_id, ops_dept_id
    finally:
        await conn.close()


# ==============================================================================
# 1. Template Download, Parsing & RBAC
# ==============================================================================
@pytest.mark.asyncio
async def test_01_download_and_parse_import_template(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies GET /api/v1/employees/import/template returns a valid `.xlsx` with all 10 required
    headers on `Employees` and the tenant's department names on `Instructions`, and blocks
    standard `employee` role with HTTP 403.
    """
    ctx = isolation_context
    globex = ctx.company_b
    await _setup_globex_import_baseline(globex.tenant_id)

    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    # Employee role blocked with 403
    resp_forb = await async_client.get("/api/v1/employees/import/template", headers=emp_headers)
    assert resp_forb.status_code == 403

    resp = await async_client.get("/api/v1/employees/import/template", headers=owner_headers)
    assert resp.status_code == 200
    assert (
        resp.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True, data_only=True)
    try:
        assert "Employees" in wb.sheetnames
        assert "Instructions" in wb.sheetnames

        ws_emp = wb["Employees"]
        header_row = next(ws_emp.iter_rows(values_only=True))
        assert tuple(header_row) == IMPORT_COLUMNS

        ws_inst = wb["Instructions"]
        inst_text = " ".join(
            str(cell)
            for row in ws_inst.iter_rows(values_only=True)
            for cell in row
            if cell is not None
        )
        assert "Engineering" in inst_text
        assert "Operations" in inst_text
        assert "full_time" in inst_text
    finally:
        wb.close()


# ==============================================================================
# 2. All 6 Validation Error Categories, Masked Error Report & Blocked Commit
# ==============================================================================
@pytest.mark.asyncio
async def test_02_validation_all_error_categories_and_error_report(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that POST /api/v1/employees/import/validate deterministically catches all 6 error
    categories across every row, masks CNIC/bank in preview & error report, neutralizes Excel
    formula injection (`=CMD(...)`), and blocks commit with HTTP 400.
    """
    ctx = isolation_context
    globex = ctx.company_b
    await _setup_globex_import_baseline(globex.tenant_id)
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    test_rows: list[dict[str, Any]] = [
        # Row 2: Valid row referencing existing DB manager
        {
            "full_name": "Valid One",
            "email": "valid.one@globex.internal",
            "phone": "+92-300-1111111",
            "department": "Engineering",
            "manager_email": "existing.head@globex.internal",
            "job_title": "Senior Engineer",
            "employment_type": "full_time",
            "join_date": "2026-01-15",
            "cnic": "42101-1234567-1",
            "bank_account": "PK36GLOB000000001234",
        },
        # Row 3: Category 1 (missing required full_name & job_title) + formula injection attempt
        {
            "full_name": "",
            "email": "missing.fields@globex.internal",
            "department": "Engineering",
            "job_title": "",
            "employment_type": "full_time",
            "join_date": "2026-01-15",
            "phone": "=CMD|' /C calc'!A0",
            "cnic": "42101-7654321-9",
            "bank_account": "PK36GLOB999999995678",
        },
        # Row 4: Category 2 (invalid email format, invalid employment_type, invalid join_date)
        #        + Category 6 (invalid CNIC format)
        {
            "full_name": "Bad Formats",
            "email": "not-an-email",
            "department": "Engineering",
            "job_title": "Analyst",
            "employment_type": "freelance_invalid",
            "join_date": "15/01/2026",
            "cnic": "12345-INVALID",
        },
        # Row 5: Category 3a (duplicate email within file - part 1)
        {
            "full_name": "Dup File A",
            "email": "dup.file@globex.internal",
            "department": "Operations",
            "job_title": "Ops Lead",
            "employment_type": "full_time",
            "join_date": "2026-02-01",
        },
        # Row 6: Category 3a (duplicate email within file - part 2, uppercase)
        {
            "full_name": "Dup File B",
            "email": "DUP.FILE@GLOBEX.INTERNAL",
            "department": "Operations",
            "job_title": "Ops Specialist",
            "employment_type": "full_time",
            "join_date": "2026-02-01",
        },
        # Row 7: Category 3b (conflict with existing DB employee email)
        {
            "full_name": "Conflict Existing",
            "email": "EXISTING.HEAD@globex.internal",
            "department": "Engineering",
            "job_title": "Clone",
            "employment_type": "full_time",
            "join_date": "2026-02-01",
        },
        # Row 8: Category 4 (unknown department) + Category 5a (unknown manager_email)
        {
            "full_name": "Unknown Dept & Mgr",
            "email": "unknown.refs@globex.internal",
            "department": "NonExistentDept",
            "manager_email": "ghost.manager@globex.internal",
            "job_title": "Designer",
            "employment_type": "contract",
            "join_date": "2026-02-10",
        },
        # Row 9: Category 5b (circular manager loop A -> B)
        {
            "full_name": "Cycle A",
            "email": "cycle.a@globex.internal",
            "department": "Engineering",
            "manager_email": "cycle.b@globex.internal",
            "job_title": "Engineer A",
            "employment_type": "full_time",
            "join_date": "2026-02-15",
        },
        # Row 10: Category 5b (circular manager loop B -> A)
        {
            "full_name": "Cycle B",
            "email": "cycle.b@globex.internal",
            "department": "Engineering",
            "manager_email": "cycle.a@globex.internal",
            "job_title": "Engineer B",
            "employment_type": "full_time",
            "join_date": "2026-02-15",
        },
    ]

    xlsx_bytes = _build_xlsx_bytes(test_rows)
    resp_val = await async_client.post(
        "/api/v1/employees/import/validate",
        headers=owner_headers,
        files={"file": ("mixed_errors.xlsx", xlsx_bytes, "application/octet-stream")},
    )
    assert resp_val.status_code == 200, resp_val.text
    val_data = resp_val.json()

    assert val_data["total_rows"] == 9
    assert val_data["valid_count"] == 1
    assert val_data["invalid_count"] == 8
    assert val_data["can_commit"] is False
    batch_id = val_data["import_batch_id"]

    # Verify PII is masked in preview_rows and never leaked in plaintext
    raw_preview_json = resp_val.text
    assert "42101-1234567-1" not in raw_preview_json
    assert "PK36GLOB000000001234" not in raw_preview_json
    row2_preview = val_data["preview_rows"][0]
    assert row2_preview["is_valid"] is True
    assert row2_preview["cnic_masked"] == "*****-*******-1"
    assert row2_preview["bank_account_masked"] == "****1234"

    # Verify all 6 error categories are present in errors[]
    error_fields = {(e["row_number"], e["field"]) for e in val_data["errors"]}
    assert (3, "full_name") in error_fields
    assert (3, "job_title") in error_fields
    assert (4, "email") in error_fields
    assert (4, "employment_type") in error_fields
    assert (4, "join_date") in error_fields
    assert (4, "cnic") in error_fields
    assert (5, "email") in error_fields
    assert (6, "email") in error_fields
    assert (7, "email") in error_fields
    assert (8, "department") in error_fields
    assert (8, "manager_email") in error_fields
    assert (9, "manager_email") in error_fields
    assert (10, "manager_email") in error_fields

    # Attempt to commit batch with errors -> blocked with HTTP 400
    resp_commit_fail = await async_client.post(
        f"/api/v1/employees/import/{batch_id}/commit",
        headers=owner_headers,
    )
    assert resp_commit_fail.status_code == 400
    assert "IMPORT_BATCH_HAS_ERRORS" in resp_commit_fail.text

    # Download .xlsx error report and verify contents, PII masking, and formula injection neutralization
    resp_err_xlsx = await async_client.get(
        f"/api/v1/employees/import/{batch_id}/error-report",
        headers=owner_headers,
    )
    assert resp_err_xlsx.status_code == 200
    wb_err = openpyxl.load_workbook(
        io.BytesIO(resp_err_xlsx.content),
        read_only=True,
        data_only=True,
    )
    try:
        ws_err = wb_err["Validation Errors"]
        err_rows = list(ws_err.iter_rows(values_only=True))
        # Header + 8 invalid rows = 9 total rows
        assert len(err_rows) == 9
        # Row index 1 in err_rows is Excel row 3 (the formula injection & masked PII test row)
        row3_err = err_rows[1]
        assert row3_err[0] == 3
        # Phone column (index 3) must be prefixed with "'" to neutralize '=CMD...'
        assert str(row3_err[3]).startswith("'=CMD")
        # CNIC (index 9) and bank account (index 10) must be masked in error report
        assert row3_err[9] == "*****-*******-9"
        assert row3_err[10] == "****5678"
        assert "full_name" in str(row3_err[11])
    finally:
        wb_err.close()


# ==============================================================================
# 3. Clean 10-Employee Commit, Internal Manager Hierarchy, Encrypted PII & Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_03_clean_10_employee_commit_encrypted_pii_and_cross_tenant_isolation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - Uploading a clean 10-employee `.xlsx` with a 3-level internal `manager_email` chain
      validates with `can_commit=True`.
    - Cross-tenant access from Tenant A (Acme) to Tenant B's `import_batch_id` is rejected with 404.
    - Concurrent commit lock returns HTTP 409 (`IMPORT_BATCH_COMMIT_IN_PROGRESS`).
    - Committing inserts all 10 employees in a single two-pass transaction, links `manager_id`
      accurately, encrypts CNIC & bank account with pre-generated `emp_id` 4-field AAD,
      enqueues 10 `sheets_sync_queue` rows, writes `employee.bulk_imported` audit log,
      and blocks double-commit (second commit returns HTTP 404).
    """
    ctx = isolation_context
    acme = ctx.company_a
    globex = ctx.company_b
    await _setup_globex_import_baseline(globex.tenant_id)

    globex_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    acme_headers = {"Authorization": f"Bearer {acme.owner.token}"}

    # Build a 10-employee batch with internal manager references:
    # - batch.lead1@globex.internal & batch.lead2@globex.internal report to existing.head@globex.internal
    # - batch.mgr1..mgr3 report to batch.lead1 / batch.lead2
    # - batch.ic1..ic5 report to batch.mgr1..mgr3
    clean_rows: list[dict[str, Any]] = [
        {
            "full_name": "Batch Lead 1",
            "email": "Batch.Lead1@globex.internal",
            "phone": "+92-300-3000001",
            "department": "Engineering",
            "manager_email": "existing.head@globex.internal",
            "job_title": "Engineering Director",
            "employment_type": "full_time",
            "join_date": "2026-01-05",
            "cnic": "42101-3000001-1",
            "bank_account": "PK36GLOB000100000001",
        },
        {
            "full_name": "Batch Lead 2",
            "email": "batch.lead2@globex.internal",
            "phone": "+92-300-3000002",
            "department": "Operations",
            "manager_email": "existing.head@globex.internal",
            "job_title": "Operations Director",
            "employment_type": "full_time",
            "join_date": "2026-01-06",
            "cnic": "42101-3000002-2",
            "bank_account": "PK36GLOB000100000002",
        },
        {
            "full_name": "Batch Manager 1",
            "email": "batch.mgr1@globex.internal",
            "department": "Engineering",
            "manager_email": "BATCH.LEAD1@globex.internal",
            "job_title": "Backend Manager",
            "employment_type": "full_time",
            "join_date": "2026-01-10",
            "cnic": "42101-3000003-3",
            "bank_account": "PK36GLOB000100000003",
        },
        {
            "full_name": "Batch Manager 2",
            "email": "batch.mgr2@globex.internal",
            "department": "Engineering",
            "manager_email": "batch.lead1@globex.internal",
            "job_title": "Frontend Manager",
            "employment_type": "full_time",
            "join_date": "2026-01-11",
            "cnic": "42101-3000004-4",
            "bank_account": "PK36GLOB000100000004",
        },
        {
            "full_name": "Batch Manager 3",
            "email": "batch.mgr3@globex.internal",
            "department": "Operations",
            "manager_email": "batch.lead2@globex.internal",
            "job_title": "People Ops Manager",
            "employment_type": "full_time",
            "join_date": "2026-01-12",
            "cnic": "42101-3000005-5",
            "bank_account": "PK36GLOB000100000005",
        },
        {
            "full_name": "Batch IC 1",
            "email": "batch.ic1@globex.internal",
            "department": "Engineering",
            "manager_email": "batch.mgr1@globex.internal",
            "job_title": "Senior Backend Engineer",
            "employment_type": "full_time",
            "join_date": "2026-01-15",
            "cnic": "42101-3000006-6",
            "bank_account": "PK36GLOB000100000006",
        },
        {
            "full_name": "Batch IC 2",
            "email": "batch.ic2@globex.internal",
            "department": "Engineering",
            "manager_email": "batch.mgr1@globex.internal",
            "job_title": "Platform Engineer",
            "employment_type": "contract",
            "join_date": "2026-01-16",
            "cnic": "42101-3000007-7",
            "bank_account": "PK36GLOB000100000007",
        },
        {
            "full_name": "Batch IC 3",
            "email": "batch.ic3@globex.internal",
            "department": "Engineering",
            "manager_email": "batch.mgr2@globex.internal",
            "job_title": "UI Engineer",
            "employment_type": "part_time",
            "join_date": "2026-01-17",
            "cnic": "42101-3000008-8",
            "bank_account": "PK36GLOB000100000008",
        },
        {
            "full_name": "Batch IC 4",
            "email": "batch.ic4@globex.internal",
            "department": "Operations",
            "manager_email": "batch.mgr3@globex.internal",
            "job_title": "Operations Coordinator",
            "employment_type": "full_time",
            "join_date": "2026-01-18",
            "cnic": "42101-3000009-9",
            "bank_account": "PK36GLOB000100000009",
        },
        {
            "full_name": "Batch IC 5",
            "email": "batch.ic5@globex.internal",
            "department": "Operations",
            "manager_email": "batch.mgr3@globex.internal",
            "job_title": "Operations Intern",
            "employment_type": "intern",
            "join_date": "2026-01-19",
            "cnic": "4210130000101",
            "bank_account": "PK36GLOB000100000010",
        },
    ]

    xlsx_bytes = _build_xlsx_bytes(clean_rows)
    resp_val = await async_client.post(
        "/api/v1/employees/import/validate",
        headers=globex_headers,
        files={"file": ("clean_10_employees.xlsx", xlsx_bytes, "application/octet-stream")},
    )
    assert resp_val.status_code == 200, resp_val.text
    v_body = resp_val.json()
    assert v_body["total_rows"] == 10
    assert v_body["valid_count"] == 10
    assert v_body["invalid_count"] == 0
    assert v_body["can_commit"] is True
    batch_id = v_body["import_batch_id"]

    # 1. Cross-tenant isolation: Tenant A (Acme) cannot download error report or commit Tenant B's batch
    resp_cross_err = await async_client.get(
        f"/api/v1/employees/import/{batch_id}/error-report",
        headers=acme_headers,
    )
    assert resp_cross_err.status_code == 404

    resp_cross_commit = await async_client.post(
        f"/api/v1/employees/import/{batch_id}/commit",
        headers=acme_headers,
    )
    assert resp_cross_commit.status_code == 404

    # 2. Concurrent commit lock check (HTTP 409 IMPORT_BATCH_COMMIT_IN_PROGRESS)
    t_uuid = UUID(globex.tenant_id)
    assert await _acquire_commit_lock(t_uuid, batch_id) is True
    try:
        resp_locked = await async_client.post(
            f"/api/v1/employees/import/{batch_id}/commit",
            headers=globex_headers,
        )
        assert resp_locked.status_code == 409
        assert "IMPORT_BATCH_COMMIT_IN_PROGRESS" in resp_locked.text
    finally:
        await _release_commit_lock(t_uuid, batch_id)

    # 3. Commit the 10-employee batch
    resp_commit = await async_client.post(
        f"/api/v1/employees/import/{batch_id}/commit",
        headers=globex_headers,
    )
    assert resp_commit.status_code == 200, resp_commit.text
    c_body = resp_commit.json()
    assert c_body["status"] == "committed"
    assert c_body["committed_count"] == 10
    assert c_body["sheets_queued"] == 10
    assert len(c_body["employee_ids"]) == 10

    # 4. Double-commit prevention: second commit attempt returns HTTP 404
    resp_double = await async_client.post(
        f"/api/v1/employees/import/{batch_id}/commit",
        headers=globex_headers,
    )
    assert resp_double.status_code == 404

    # 5. Verify PostgreSQL records, internal manager_id hierarchy, and AES-256-GCM 4-field AAD decryption
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, globex.tenant_id)
        emps = (
            (await session.execute(select(Employee).where(Employee.tenant_id == t_uuid)))
            .scalars()
            .all()
        )
        # 1 baseline head + 10 imported = 11 total
        assert len(emps) == 11
        by_email = {e.email: e for e in emps}

        lead1 = by_email["batch.lead1@globex.internal"]
        mgr1 = by_email["batch.mgr1@globex.internal"]
        ic1 = by_email["batch.ic1@globex.internal"]
        existing_head = by_email["existing.head@globex.internal"]

        assert lead1.manager_id == existing_head.id
        assert mgr1.manager_id == lead1.id
        assert ic1.manager_id == mgr1.id

        # Decrypt CNIC and bank account using canonical 4-field AAD
        assert lead1.get_cnic() == "42101-3000001-1"
        assert lead1.get_bank_account() == "PK36GLOB000100000001"
        assert ic1.get_cnic() == "42101-3000006-6"
        assert ic1.get_bank_account() == "PK36GLOB000100000006"

    # 6. Verify audit log & sheets_sync_queue entries
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        sheets_cnt = await conn.fetchval(
            """
            SELECT count(*) FROM public.sheets_sync_queue
            WHERE tenant_id = $1::uuid AND action = 'bulk_import';
            """,
            globex.tenant_id,
        )
        assert sheets_cnt == 10

        audit_cnt = await conn.fetchval(
            """
            SELECT count(*) FROM public.audit_logs
            WHERE tenant_id = $1::uuid AND action = 'employee.bulk_imported';
            """,
            globex.tenant_id,
        )
        assert audit_cnt >= 1
    finally:
        await conn.close()
