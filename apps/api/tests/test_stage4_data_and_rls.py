"""
Comprehensive Data Layer, Encrypted PII, Secure Views & RLS Regression Suite for Stage 4 Prompt 1.

Verifies:
1. AES-256-GCM envelope encryption/decryption round-trip for employee `cnic` and `bank_account`
   using canonical 4-field AAD `(tenant_id, "employee", employee_id, field_name)` + masking helpers.
2. Cryptographic AAD tamper rejection across all 4 AAD dimensions (`tenant_id`, `employee_id`,
   `field_name` swap `cnic` <-> `bank_account`, `entity_type`) and ciphertext tampering.
3. `public.pub_employees` view metadata (`security_invoker=true`, `security_barrier=true`,
   owner `agentic_hr_migrator`, `NOBYPASSRLS`) and strict exclusion of `cnic_encrypted`,
   `bank_account_encrypted`, and salary fields.
4. `public.pub_documents_expiring` view metadata (`security_invoker=true`, `security_barrier=true`,
   owner `agentic_hr_migrator`, `NOBYPASSRLS`), inclusion of `employee_status`, exclusion of
   `expiry_date IS NULL` rows, and exact `days_until_expiry` calculation in `Asia/Karachi`.
5. 40-employee seed idempotency for `Acme Tech Solutions`: running `seed_database()` twice produces
   exactly 4 departments, 40 employees, a valid acyclic multi-level `manager_id` hierarchy (depth >= 3),
   decryptable CNIC & bank accounts on all 40 employees, and sample documents expiring in 30, 7, and 1 days.
6. Live RLS & cross-tenant isolation across all 6 new tables (`departments`, `employees`,
   `employee_documents`, `policy_documents`, `onboarding_state`, `notifications`) and both views
   (`pub_employees`, `pub_documents_expiring`) under `agentic_hr_app`.
7. Case-insensitive uniqueness on `departments (tenant_id, lower(name))` and
   `employees (tenant_id, lower(email))`, plus partial uniqueness on
   `notifications (tenant_id, dedupe_key) WHERE dedupe_key IS NOT NULL`.
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import select

from app.core.encryption import (
    AuthenticationFailedError,
    EncryptionContext,
    KeyRegistry,
    LocalKeyProvider,
    decrypt_field,
)
from app.core.timezones import to_karachi
from app.db.session import AsyncSessionLocal
from app.models import (
    Department,
    Employee,
    PolicyDocument,
    SyntheticPolicyDocument,
)
from app.models.tenant import set_tenant_context
from app.services.employee_service import EmployeeService
from scripts.seed import seed_database
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    MultiTenantTestContext,
    assert_table_tenant_isolation,
    get_app_connection,
)

STAGE4_TABLES: tuple[str, ...] = (
    "departments",
    "employees",
    "employee_documents",
    "policy_documents",
    "onboarding_state",
    "notifications",
)

STAGE4_VIEWS: tuple[str, ...] = (
    "pub_employees",
    "pub_documents_expiring",
)


# ==============================================================================
# 1. Encryption Round-Trip, Masking & Export Alias Verification
# ==============================================================================
def test_01_employee_pii_encryption_roundtrip_and_masking() -> None:
    """
    Verifies EmployeeService & Employee model CNIC and bank account encryption round-trip,
    masking helpers ('*****-*******-1', '****1234'), and PolicyDocument / SyntheticPolicyDocument exports.
    """
    key_bytes = b"\x22" * 32
    provider = LocalKeyProvider(KeyRegistry("test-stage4-kek-v1", key_bytes))

    tenant_id = uuid.uuid4()
    employee_id = uuid.uuid4()
    raw_cnic = "42101-1234567-1"
    raw_iban = "PK36MEZN0002810101231234"

    enc_cnic = EmployeeService.encrypt_cnic(
        tenant_id=tenant_id,
        employee_id=employee_id,
        plaintext_cnic=raw_cnic,
        key_provider=provider,
    )
    enc_iban = EmployeeService.encrypt_bank_account(
        tenant_id=tenant_id,
        employee_id=employee_id,
        plaintext_bank_account=raw_iban,
        key_provider=provider,
    )

    assert raw_cnic not in enc_cnic
    assert raw_iban not in enc_iban

    dec_cnic = EmployeeService.decrypt_cnic(
        tenant_id=str(tenant_id).upper(),
        employee_id=str(employee_id).upper(),
        encrypted_cnic=enc_cnic,
        key_provider=provider,
    )
    dec_iban = EmployeeService.decrypt_bank_account(
        tenant_id=tenant_id,
        employee_id=employee_id,
        encrypted_bank_account=enc_iban,
        key_provider=provider,
    )
    assert dec_cnic == raw_cnic
    assert dec_iban == raw_iban

    # Verify masking helpers
    assert EmployeeService.mask_cnic(raw_cnic) == "*****-*******-1"
    assert EmployeeService.mask_cnic("4210112345679") == "*****-*******-9"
    assert EmployeeService.mask_cnic(None) is None
    assert EmployeeService.mask_cnic("   ") is None

    assert EmployeeService.mask_bank_account(raw_iban) == "****1234"
    assert EmployeeService.mask_bank_account("1234") == "****"
    assert EmployeeService.mask_bank_account(None) is None
    assert EmployeeService.mask_bank_account("   ") is None

    # Verify Employee ORM instance helper methods
    emp = Employee(
        id=employee_id,
        tenant_id=tenant_id,
        full_name="Test Employee",
        email="test.employee@acmetech.internal",
        job_title="Software Engineer",
        join_date=date(2024, 1, 1),
    )
    emp.set_cnic(raw_cnic, key_provider=provider)
    emp.set_bank_account(raw_iban, key_provider=provider)

    assert emp.get_cnic(key_provider=provider) == raw_cnic
    assert emp.get_masked_cnic(key_provider=provider) == "*****-*******-1"
    assert emp.get_bank_account(key_provider=provider) == raw_iban
    assert emp.get_masked_bank_account(key_provider=provider) == "****1234"

    # Verify PolicyDocument (ORM) and SyntheticPolicyDocument (Stage 1 Pydantic) coexist cleanly
    assert PolicyDocument.__tablename__ == "policy_documents"
    syn_doc = SyntheticPolicyDocument(
        title="Leave Policy",
        language="en",
        category="HR",
        summary="20 days annual leave",
    )
    assert syn_doc.title == "Leave Policy"


# ==============================================================================
# 2. Cryptographic AAD Tamper Rejection Across All 4 Dimensions
# ==============================================================================
def test_02_employee_pii_aad_tamper_rejection() -> None:
    """
    Verifies that decrypting an employee's CNIC or bank account fails closed with
    AuthenticationFailedError if any of the 4 AAD fields (tenant_id, entity_type,
    record_id/employee_id, field_name) or the ciphertext is tampered with.
    """
    key_bytes = b"\x33" * 32
    provider = LocalKeyProvider(KeyRegistry("test-stage4-kek-v1", key_bytes))

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    emp_1 = uuid.uuid4()
    emp_2 = uuid.uuid4()

    enc_cnic = EmployeeService.encrypt_cnic(
        tenant_id=tenant_a,
        employee_id=emp_1,
        plaintext_cnic="42101-7654321-3",
        key_provider=provider,
    )

    # 1. Wrong tenant_id -> AuthenticationFailedError
    with pytest.raises(AuthenticationFailedError):
        EmployeeService.decrypt_cnic(
            tenant_id=tenant_b,
            employee_id=emp_1,
            encrypted_cnic=enc_cnic,
            key_provider=provider,
        )

    # 2. Wrong employee_id -> AuthenticationFailedError
    with pytest.raises(AuthenticationFailedError):
        EmployeeService.decrypt_cnic(
            tenant_id=tenant_a,
            employee_id=emp_2,
            encrypted_cnic=enc_cnic,
            key_provider=provider,
        )

    # 3. Swapped field_name (attempting to decrypt CNIC ciphertext as bank_account)
    with pytest.raises(AuthenticationFailedError):
        EmployeeService.decrypt_bank_account(
            tenant_id=tenant_a,
            employee_id=emp_1,
            encrypted_bank_account=enc_cnic,
            key_provider=provider,
        )

    # 4. Wrong entity_type ('candidate' instead of 'employee')
    wrong_entity_ctx = EncryptionContext(
        tenant_id=tenant_a,
        entity_type="candidate",
        record_id=emp_1,
        field_name="cnic",
    )
    with pytest.raises(AuthenticationFailedError):
        decrypt_field(enc_cnic, wrong_entity_ctx, key_provider=provider)

    # 5. Tampered ciphertext byte
    envelope_dict = json.loads(enc_cnic)
    ct = envelope_dict["ciphertext"]
    flipped_char = "A" if ct[0] != "A" else "B"
    envelope_dict["ciphertext"] = flipped_char + ct[1:]
    with pytest.raises(AuthenticationFailedError):
        EmployeeService.decrypt_cnic(
            tenant_id=tenant_a,
            employee_id=emp_1,
            encrypted_cnic=json.dumps(envelope_dict),
            key_provider=provider,
        )


# ==============================================================================
# 3. Secure Read View Metadata & `pub_employees` Strict Column Exclusion
# ==============================================================================
@pytest.mark.asyncio
async def test_03_secure_views_metadata_and_pub_employees_column_exclusion() -> None:
    """
    Verifies that both `pub_employees` and `pub_documents_expiring`:
    - Are configured with `security_invoker=true` and `security_barrier=true`.
    - Are owned by `agentic_hr_migrator` (non-superuser, NOBYPASSRLS).
    - Are strictly SELECT-only for `agentic_hr_app` (DELETE/UPDATE/INSERT rejected).
    - `pub_employees` exposes all 16 non-sensitive columns and NEVER exposes
      `cnic_encrypted`, `bank_account_encrypted`, or salary fields.
    """
    conn = await get_app_connection()
    try:
        for view_name in STAGE4_VIEWS:
            meta = await conn.fetchrow(
                """
                SELECT c.reloptions, r.rolname AS owner_name, r.rolsuper, r.rolbypassrls
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
                WHERE n.nspname = 'public' AND c.relname = $1 AND c.relkind = 'v';
                """,
                view_name,
            )
            assert meta is not None, f"View public.{view_name} does not exist"
            reloptions = meta["reloptions"] or []
            assert "security_invoker=true" in reloptions, (
                f"View public.{view_name} missing security_invoker=true: {reloptions}"
            )
            assert "security_barrier=true" in reloptions, (
                f"View public.{view_name} missing security_barrier=true: {reloptions}"
            )
            assert meta["owner_name"] == "agentic_hr_migrator"
            assert meta["rolsuper"] is False
            assert meta["rolbypassrls"] is False

            # Mutation privileges must be revoked from agentic_hr_app
            priv_rows = await conn.fetch(
                """
                SELECT
                    has_table_privilege('agentic_hr_app', $1, 'SELECT') AS can_select,
                    has_table_privilege('agentic_hr_app', $1, 'INSERT') AS can_insert,
                    has_table_privilege('agentic_hr_app', $1, 'UPDATE') AS can_update,
                    has_table_privilege('agentic_hr_app', $1, 'DELETE') AS can_delete,
                    has_table_privilege('agentic_hr_app', $1, 'TRUNCATE') AS can_truncate;
                """,
                f"public.{view_name}",
            )
            assert len(priv_rows) == 1
            priv = priv_rows[0]
            assert priv["can_select"] is True, f"View public.{view_name} must grant SELECT"
            assert priv["can_insert"] is False, f"View public.{view_name} must deny INSERT"
            assert priv["can_update"] is False, f"View public.{view_name} must deny UPDATE"
            assert priv["can_delete"] is False, f"View public.{view_name} must deny DELETE"
            assert priv["can_truncate"] is False, f"View public.{view_name} must deny TRUNCATE"

            with pytest.raises(
                (
                    asyncpg.exceptions.InsufficientPrivilegeError,
                    asyncpg.exceptions.ObjectNotInPrerequisiteStateError,
                )
            ):
                await conn.execute(f"DELETE FROM public.{view_name};")

        # Inspect pub_employees columns in information_schema
        col_rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'pub_employees'
            ORDER BY ordinal_position;
            """
        )
        col_names = [r["column_name"] for r in col_rows]
        expected_cols = [
            "id",
            "tenant_id",
            "user_id",
            "employee_code",
            "full_name",
            "email",
            "phone",
            "department_id",
            "manager_id",
            "job_title",
            "employment_type",
            "join_date",
            "leaving_date",
            "status",
            "created_at",
            "updated_at",
        ]
        assert col_names == expected_cols
        forbidden_substrings = ("cnic", "bank", "iban", "salary", "compensation")
        for col in col_names:
            for forbidden in forbidden_substrings:
                assert forbidden not in col.lower(), (
                    f"Forbidden sensitive column '{col}' exposed in public.pub_employees!"
                )
    finally:
        await conn.close()


# ==============================================================================
# 4. 40-Employee Seed Idempotency, Org-Chart Hierarchy & `pub_documents_expiring`
# ==============================================================================
@pytest.mark.asyncio
async def test_04_seed_idempotency_40_employees_org_chart_and_expiring_view(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that running `seed_database()` twice is idempotent and populates Acme Tech Solutions
    with:
    - Exactly 4 departments (`Engineering`, `Human Resources`, `Product & Design`, `Finance & Operations`)
      each wired to a valid `head_employee_id`.
    - Exactly 40 synthetic employees with a valid acyclic multi-level `manager_id` hierarchy (depth >= 3).
    - Valid AES-256-GCM encrypted CNIC and bank account on all 40 employees.
    - Sample documents expiring in 30, 7, and 1 days (relative to Asia/Karachi) accurately surfaced
      by `public.pub_documents_expiring` with `employee_status`, while non-expiring documents
      (`expiry_date IS NULL`) are excluded.
    """
    await seed_database()
    await seed_database()

    acme_tenant_id = isolation_context.company_a.tenant_id

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, acme_tenant_id)

        # 1. Verify 4 Departments
        depts = (await session.execute(select(Department))).scalars().all()
        assert len(depts) == 4
        dept_names = {d.name for d in depts}
        assert dept_names == {
            "Engineering",
            "Human Resources",
            "Product & Design",
            "Finance & Operations",
        }

        # 2. Verify 40 Employees
        emps = (await session.execute(select(Employee))).scalars().all()
        assert len(emps) == 40
        emps_by_id = {e.id: e for e in emps}

        # All 4 department heads must point to valid employees in Acme Tech
        for d in depts:
            assert d.head_employee_id is not None
            assert d.head_employee_id in emps_by_id

        # 3. Verify acyclic multi-level manager_id hierarchy (depth >= 3)
        roots = [e for e in emps if e.manager_id is None]
        assert len(roots) == 1
        assert roots[0].employee_code == "EMP-001"

        max_depth = 0
        for e in emps:
            visited: set[uuid.UUID] = set()
            curr: Employee | None = e
            depth = 0
            while curr is not None and curr.manager_id is not None:
                assert curr.id not in visited, (
                    f"Cycle detected in manager_id hierarchy at {curr.id}"
                )
                visited.add(curr.id)
                assert curr.manager_id in emps_by_id, "Manager must belong to same tenant"
                curr = emps_by_id[curr.manager_id]
                depth += 1
            max_depth = max(max_depth, depth)

        assert max_depth >= 3, f"Expected multi-level org chart depth >= 3, got {max_depth}"

        # 4. Verify all 40 employees have decryptable CNIC and bank account + valid masks
        for e in emps:
            assert e.cnic_encrypted is not None
            assert e.bank_account_encrypted is not None
            plain_cnic = e.get_cnic()
            plain_bank = e.get_bank_account()
            assert plain_cnic is not None and plain_cnic.startswith("42101-")
            assert plain_bank is not None and plain_bank.startswith("PK36ACME")
            masked_cnic = e.get_masked_cnic()
            masked_bank = e.get_masked_bank_account()
            assert masked_cnic is not None and masked_cnic.startswith("*****-*******-")
            assert (
                masked_bank is not None and masked_bank.startswith("****") and len(masked_bank) == 8
            )

    # 5. Verify pub_employees and pub_documents_expiring via agentic_hr_app connection
    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true);", acme_tenant_id)

            pub_emp_count = await conn.fetchval("SELECT count(*) FROM public.pub_employees;")
            assert pub_emp_count == 40

            expiring_rows = await conn.fetch(
                """
                SELECT
                    document_id,
                    tenant_id,
                    employee_id,
                    employee_name,
                    employee_email,
                    employee_status,
                    document_type,
                    title,
                    expiry_date,
                    days_until_expiry
                FROM public.pub_documents_expiring
                ORDER BY days_until_expiry ASC;
                """
            )
            # 4 seeded docs have expiry_date IS NOT NULL (1, 7, 30, 180); 1 has expiry_date IS NULL
            days_set = {r["days_until_expiry"] for r in expiring_rows}
            assert {1, 7, 30}.issubset(days_set), (
                f"Expected expiring docs at 1, 7, 30 days; got {days_set}"
            )
            for r in expiring_rows:
                assert r["expiry_date"] is not None
                assert r["employee_status"] in (
                    "active",
                    "invited",
                    "on_leave",
                    "terminated",
                    "resigned",
                )
    finally:
        await conn.close()


# ==============================================================================
# 5. Live RLS Enforcement: Bogus Tenant Returns 0 Rows & Cross-Tenant Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_05_stage4_tables_and_views_rls_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    1. Connecting as `agentic_hr_app` with a bogus tenant UUID returns 0 rows on all 6 Stage 4
       tables AND both views (`pub_employees`, `pub_documents_expiring`).
    2. Full 6-point multi-tenant isolation (`assert_table_tenant_isolation`) between
       Company A (Acme Tech Solutions) and Company B (Globex Corporation) across all 6 tables.
    3. Cross-tenant isolation on `pub_employees` and `pub_documents_expiring`.
    """
    ctx = isolation_context
    tenant_a = ctx.company_a.tenant_id
    tenant_b = ctx.company_b.tenant_id
    bogus_tenant = "00000000-0000-0000-0000-000000000000"

    # Provision one test row in each of the 6 tables for Company A and Company B
    suffix = uuid.uuid4().hex[:8]
    today_pkt = to_karachi(datetime.now(UTC)).date()

    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    created_ids: dict[str, tuple[str, str]] = {}
    try:
        # Clean any prior onboarding_state for Company A / B before inserting test rows
        await admin_conn.execute(
            "DELETE FROM public.onboarding_state WHERE tenant_id IN ($1::uuid, $2::uuid);",
            tenant_a,
            tenant_b,
        )

        for label, t_id in (("a", tenant_a), ("b", tenant_b)):
            dept_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.departments (tenant_id, name, code)
                    VALUES ($1::uuid, $2, $3)
                    RETURNING id;
                    """,
                    t_id,
                    f"RLS Dept {label} {suffix}",
                    f"RLS-{label.upper()}",
                )
            )
            emp_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.employees (
                        tenant_id, department_id, full_name, email, job_title, join_date, status
                    )
                    VALUES ($1::uuid, $2::uuid, $3, $4, 'Security Engineer', $5, 'active')
                    RETURNING id;
                    """,
                    t_id,
                    dept_id,
                    f"RLS Employee {label}",
                    f"rls.{label}.{suffix}@example.com",
                    today_pkt,
                )
            )
            doc_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.employee_documents (
                        tenant_id, employee_id, document_type, title, file_name,
                        mime_type, size_bytes, storage_provider, storage_key, expiry_date
                    )
                    VALUES (
                        $1::uuid, $2::uuid, 'cnic', $3, 'cnic.pdf',
                        'application/pdf', 1024, 'local', $4, $5
                    )
                    RETURNING id;
                    """,
                    t_id,
                    emp_id,
                    f"RLS Doc {label}",
                    f"rls/{label}/{suffix}/cnic.pdf",
                    today_pkt + timedelta(days=15),
                )
            )
            pol_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.policy_documents (
                        tenant_id, title, file_name, mime_type, size_bytes,
                        storage_provider, storage_key
                    )
                    VALUES (
                        $1::uuid, $2, 'policy.pdf', 'application/pdf', 2048,
                        'local', $3
                    )
                    RETURNING id;
                    """,
                    t_id,
                    f"RLS Policy {label}",
                    f"rls/{label}/{suffix}/policy.pdf",
                )
            )
            onb_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.onboarding_state (tenant_id, current_step, business_type)
                    VALUES ($1::uuid, 2, $2)
                    RETURNING id;
                    """,
                    t_id,
                    f"SaaS-{label}",
                )
            )
            notif_id = str(
                await admin_conn.fetchval(
                    """
                    INSERT INTO public.notifications (
                        tenant_id, category, severity, title, message, dedupe_key
                    )
                    VALUES ($1::uuid, 'system', 'info', $2, 'RLS check', $3)
                    RETURNING id;
                    """,
                    t_id,
                    f"RLS Notif {label}",
                    f"rls-dedupe-{label}-{suffix}",
                )
            )

            if label == "a":
                created_ids["departments"] = (dept_id, "")
                created_ids["employees"] = (emp_id, "")
                created_ids["employee_documents"] = (doc_id, "")
                created_ids["policy_documents"] = (pol_id, "")
                created_ids["onboarding_state"] = (onb_id, "")
                created_ids["notifications"] = (notif_id, "")
            else:
                created_ids["departments"] = (created_ids["departments"][0], dept_id)
                created_ids["employees"] = (created_ids["employees"][0], emp_id)
                created_ids["employee_documents"] = (created_ids["employee_documents"][0], doc_id)
                created_ids["policy_documents"] = (created_ids["policy_documents"][0], pol_id)
                created_ids["onboarding_state"] = (created_ids["onboarding_state"][0], onb_id)
                created_ids["notifications"] = (created_ids["notifications"][0], notif_id)

        # 1. Verify bogus tenant returns 0 rows across all 6 tables + 2 views under agentic_hr_app
        app_conn = await get_app_connection()
        try:
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    bogus_tenant,
                )
                for relation in (*STAGE4_TABLES, *STAGE4_VIEWS):
                    cnt = await app_conn.fetchval(f"SELECT count(*) FROM public.{relation};")
                    assert cnt == 0, (
                        f"CRITICAL RLS LEAK: public.{relation} returned {cnt} rows under bogus tenant!"
                    )

            # 2. Verify cross-tenant isolation on pub_employees and pub_documents_expiring under Tenant B
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    tenant_b,
                )
                emp_a_id = created_ids["employees"][0]
                emp_b_id = created_ids["employees"][1]
                doc_a_id = created_ids["employee_documents"][0]
                doc_b_id = created_ids["employee_documents"][1]

                pub_emp_rows = await app_conn.fetch("SELECT id FROM public.pub_employees;")
                pub_emp_ids = {str(r["id"]) for r in pub_emp_rows}
                assert emp_b_id in pub_emp_ids
                assert emp_a_id not in pub_emp_ids

                pub_doc_rows = await app_conn.fetch(
                    "SELECT document_id, days_until_expiry FROM public.pub_documents_expiring;"
                )
                pub_doc_map = {str(r["document_id"]): r["days_until_expiry"] for r in pub_doc_rows}
                assert doc_b_id in pub_doc_map
                assert pub_doc_map[doc_b_id] == 15
                assert doc_a_id not in pub_doc_map
        finally:
            await app_conn.close()

        # 3. Run full 6-point assert_table_tenant_isolation on all 6 tables
        await assert_table_tenant_isolation(
            table_name="departments",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["departments"][0],
            company_b_row_id=created_ids["departments"][1],
            insert_sql_factory=lambda target_tid: (
                "INSERT INTO public.departments (tenant_id, name) VALUES ($1::uuid, $2);",
                [target_tid, f"Cross Dept {uuid.uuid4().hex[:6]}"],
            ),
        )
        await assert_table_tenant_isolation(
            table_name="employees",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["employees"][0],
            company_b_row_id=created_ids["employees"][1],
            insert_sql_factory=lambda target_tid: (
                (
                    "INSERT INTO public.employees (tenant_id, full_name, email, job_title, join_date) "
                    "VALUES ($1::uuid, 'Cross Emp', $2, 'Dev', $3);"
                ),
                [target_tid, f"cross.{uuid.uuid4().hex[:6]}@example.com", today_pkt],
            ),
        )
        await assert_table_tenant_isolation(
            table_name="employee_documents",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["employee_documents"][0],
            company_b_row_id=created_ids["employee_documents"][1],
            insert_sql_factory=lambda target_tid: (
                (
                    "INSERT INTO public.employee_documents "
                    "(tenant_id, employee_id, document_type, title, file_name, mime_type, storage_key) "
                    "VALUES ($1::uuid, $2::uuid, 'other', 'Cross Doc', 'c.pdf', 'application/pdf', 'k');"
                ),
                [target_tid, created_ids["employees"][1]],
            ),
        )
        await assert_table_tenant_isolation(
            table_name="policy_documents",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["policy_documents"][0],
            company_b_row_id=created_ids["policy_documents"][1],
            insert_sql_factory=lambda target_tid: (
                (
                    "INSERT INTO public.policy_documents "
                    "(tenant_id, title, file_name, mime_type, storage_key) "
                    "VALUES ($1::uuid, 'Cross Policy', 'p.pdf', 'application/pdf', 'k');"
                ),
                [target_tid],
            ),
        )
        await assert_table_tenant_isolation(
            table_name="onboarding_state",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["onboarding_state"][0],
            company_b_row_id=created_ids["onboarding_state"][1],
            insert_sql_factory=lambda target_tid: (
                "INSERT INTO public.onboarding_state (tenant_id, current_step) VALUES ($1::uuid, 1);",
                [target_tid],
            ),
        )
        await assert_table_tenant_isolation(
            table_name="notifications",
            company_a_tenant_id=tenant_a,
            company_b_tenant_id=tenant_b,
            company_a_row_id=created_ids["notifications"][0],
            company_b_row_id=created_ids["notifications"][1],
            insert_sql_factory=lambda target_tid: (
                (
                    "INSERT INTO public.notifications (tenant_id, category, title, message) "
                    "VALUES ($1::uuid, 'system', 'Cross Notif', 'Body');"
                ),
                [target_tid],
            ),
        )
    finally:
        # Cleanup temporary isolation rows
        for tbl in reversed(STAGE4_TABLES):
            if tbl in created_ids:
                id_a, id_b = created_ids[tbl]
                await admin_conn.execute(
                    f"DELETE FROM public.{tbl} WHERE id IN ($1::uuid, $2::uuid);",
                    id_a,
                    id_b,
                )
        await admin_conn.close()


# ==============================================================================
# 6. Case-Insensitive & Partial Unique Index Enforcement
# ==============================================================================
@pytest.mark.asyncio
async def test_06_unique_indexes_and_notification_dedupe_partial_index(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - `departments`: case-insensitive unique `(tenant_id, lower(name))` rejects duplicate name.
    - `employees`: case-insensitive unique `(tenant_id, lower(email))` rejects duplicate email.
    - `notifications`: partial unique `(tenant_id, dedupe_key) WHERE dedupe_key IS NOT NULL`
      rejects duplicate non-null dedupe_key within the same tenant, while allowing multiple
      notifications with `dedupe_key IS NULL`.
    """
    tenant_b = isolation_context.company_b.tenant_id
    suffix = uuid.uuid4().hex[:8]
    conn = await get_app_connection()
    try:
        # 1. Case-insensitive department name uniqueness
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true);", tenant_b)
                await conn.execute(
                    "INSERT INTO public.departments (tenant_id, name) VALUES ($1::uuid, $2);",
                    tenant_b,
                    f"Data Science {suffix}",
                )
                await conn.execute(
                    "INSERT INTO public.departments (tenant_id, name) VALUES ($1::uuid, $2);",
                    tenant_b,
                    f"data science {suffix}",
                )

        # 2. Case-insensitive employee email uniqueness
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true);", tenant_b)
                await conn.execute(
                    """
                    INSERT INTO public.employees (tenant_id, full_name, email, job_title, join_date)
                    VALUES ($1::uuid, 'User One', $2, 'Engineer', CURRENT_DATE);
                    """,
                    tenant_b,
                    f"Duplicate.{suffix}@Globex.com",
                )
                await conn.execute(
                    """
                    INSERT INTO public.employees (tenant_id, full_name, email, job_title, join_date)
                    VALUES ($1::uuid, 'User Two', $2, 'Engineer', CURRENT_DATE);
                    """,
                    tenant_b,
                    f"duplicate.{suffix}@globex.com",
                )

        # 3. Notifications partial unique index on (tenant_id, dedupe_key) WHERE dedupe_key IS NOT NULL
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true);", tenant_b)
                # Multiple NULL dedupe_keys must succeed
                await conn.execute(
                    """
                    INSERT INTO public.notifications (tenant_id, category, title, message, dedupe_key)
                    VALUES
                        ($1::uuid, 'system', 'Null Key 1', 'Msg 1', NULL),
                        ($1::uuid, 'system', 'Null Key 2', 'Msg 2', NULL);
                    """,
                    tenant_b,
                )
                # Duplicate non-null dedupe_key must raise UniqueViolationError
                await conn.execute(
                    """
                    INSERT INTO public.notifications (tenant_id, category, title, message, dedupe_key)
                    VALUES ($1::uuid, 'document_expiry', 'Expiry 1', 'Msg', $2);
                    """,
                    tenant_b,
                    f"doc-exp-{suffix}",
                )
                await conn.execute(
                    """
                    INSERT INTO public.notifications (tenant_id, category, title, message, dedupe_key)
                    VALUES ($1::uuid, 'document_expiry', 'Expiry 2', 'Msg', $2);
                    """,
                    tenant_b,
                    f"doc-exp-{suffix}",
                )
    finally:
        await conn.close()
