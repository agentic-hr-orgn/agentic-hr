"""
Comprehensive Multi-Tenant Isolation Test Suite for Agentic HR Core.
Complies with ADR 002, ADR 003, ADR 006, and Stage 2 Phase 4 specifications.

Executes all 10 mandatory negative tests at both PostgreSQL RLS and FastAPI API layers:
1. Company A cannot read Company B records.
2. Company A cannot update Company B records.
3. Company A cannot delete Company B records.
4. Company A cannot insert records into Company B.
5. User cannot access tenant records without an active tenant.
6. User cannot access a tenant without membership.
7. User cannot change tenant_id through request parameters.
8. User cannot access another tenant after organization switching incorrectly.
9. Missing database tenant context returns no unauthorized records.
10. Database connection pooling does not leak tenant context.

All RLS tests execute strictly using the restricted application role: agentic_hr_app.
"""

import asyncio
import random
import uuid

import asyncpg
import httpx
import pytest

from app.core.clerk import register_test_signing_key
from app.core.encryption import EncryptionContext, encrypt_field
from tests.isolation_harness import (
    APP_DB_URL,
    MIGRATION_DB_URL,
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
    assert_route_tenant_isolation,
    assert_table_tenant_isolation,
    get_app_connection,
)


@pytest.fixture(autouse=True)
def setup_isolation_keys():
    """Ensure isolation test signing keys are registered for every test."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    yield


# ===========================================================================
# 1. DIRECT POSTGRESQL ROW-LEVEL SECURITY (RLS) ISOLATION TESTS
#    Connecting strictly as agentic_hr_app (NOSUPERUSER, NOBYPASSRLS)
# ===========================================================================


@pytest.mark.asyncio
async def test_rls_negative_01_read_cross_tenant_blocked(
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 1: Company A cannot read Company B records at the database level."""
    ctx = isolation_context
    conn = await get_app_connection()
    try:
        async with conn.transaction():
            # Establish Company A context
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                "SELECT set_config('app.clerk_user_id', $1, true);",
                ctx.company_a.owner.clerk_user_id,
            )

            # 1a. Company B memberships are invisible (0 rows)
            b_memberships = await conn.fetch(
                "SELECT * FROM public.memberships WHERE tenant_id = $1::uuid;",
                ctx.company_b.tenant_id,
            )
            assert len(b_memberships) == 0, f"Leaked Company B memberships: {b_memberships}"

            # 1b. Company B tenant record is invisible (0 rows)
            b_tenant = await conn.fetch(
                "SELECT * FROM public.tenants WHERE id = $1::uuid;",
                ctx.company_b.tenant_id,
            )
            assert len(b_tenant) == 0, f"Leaked Company B tenant record: {b_tenant}"

            # 1c. Exclusive Company B users are invisible (0 rows)
            # Globex Owner belongs exclusively to Company B
            b_user = await conn.fetch(
                "SELECT * FROM public.users WHERE id = $1::uuid;",
                ctx.company_b.owner.user_id,
            )
            assert len(b_user) == 0, f"Leaked exclusive Company B user to Company A: {b_user}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_negative_02_update_cross_tenant_blocked(
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 2: Company A cannot update Company B records at the database level."""
    ctx = isolation_context
    conn = await get_app_connection()
    try:
        # 2a. Update Company B membership affects 0 rows
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                "SELECT set_config('app.clerk_user_id', $1, true);",
                ctx.company_a.owner.clerk_user_id,
            )
            status_mem = await conn.execute(
                "UPDATE public.memberships SET is_hr_head = true WHERE tenant_id = $1::uuid;",
                ctx.company_b.tenant_id,
            )
            assert status_mem == "UPDATE 0", (
                f"CRITICAL: Cross-tenant UPDATE modified memberships: {status_mem}"
            )

        # 2b. Update Company B tenant record affects 0 rows or is blocked by privileges
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                "SELECT set_config('app.clerk_user_id', $1, true);",
                ctx.company_a.owner.clerk_user_id,
            )
            tenant_update_blocked = False
            try:
                status_tenant = await conn.execute(
                    "UPDATE public.tenants SET name = 'Attacked' WHERE id = $1::uuid;",
                    ctx.company_b.tenant_id,
                )
                if status_tenant == "UPDATE 0":
                    tenant_update_blocked = True
            except (
                asyncpg.exceptions.InsufficientPrivilegeError,
                asyncpg.exceptions.CheckViolationError,
            ):
                tenant_update_blocked = True
            assert tenant_update_blocked, "CRITICAL: Cross-tenant UPDATE modified tenants!"

        # 2c. Update exclusive Company B user affects 0 rows
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                "SELECT set_config('app.clerk_user_id', $1, true);",
                ctx.company_a.owner.clerk_user_id,
            )
            status_user = await conn.execute(
                "UPDATE public.users SET name = 'Attacked' WHERE id = $1::uuid;",
                ctx.company_b.owner.user_id,
            )
            assert status_user == "UPDATE 0", (
                f"CRITICAL: Cross-tenant UPDATE modified users: {status_user}"
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_negative_03_delete_cross_tenant_blocked(
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 3: Company A cannot delete Company B records at the database level."""
    ctx = isolation_context
    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                "SELECT set_config('app.clerk_user_id', $1, true);",
                ctx.company_a.owner.clerk_user_id,
            )

            # 3a. Delete Company B membership affects 0 rows
            status_mem = await conn.execute(
                "DELETE FROM public.memberships WHERE tenant_id = $1::uuid;",
                ctx.company_b.tenant_id,
            )
            assert status_mem == "DELETE 0", (
                f"CRITICAL: Cross-tenant DELETE removed memberships: {status_mem}"
            )

            # 3b. Delete on users table is blocked by column privileges / RLS
            user_delete_blocked = False
            try:
                status_user = await conn.execute(
                    "DELETE FROM public.users WHERE id = $1::uuid;",
                    ctx.company_b.owner.user_id,
                )
                assert status_user == "DELETE 0"
                user_delete_blocked = True
            except asyncpg.exceptions.InsufficientPrivilegeError:
                user_delete_blocked = True
            assert user_delete_blocked, "CRITICAL: Cross-tenant DELETE succeeded on users!"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_negative_04_insert_cross_tenant_blocked(
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 4: Company A cannot insert records into Company B at the database level."""
    ctx = isolation_context
    conn = await get_app_connection()
    try:
        # Fetch a valid role ID for insert test
        role_row = await conn.fetchrow("SELECT id FROM public.roles WHERE role_key = 'employee';")
        assert role_row is not None
        role_id = role_row["id"]

        # 4a. Attempt to insert membership with Company B's tenant_id must fail WITH CHECK policy
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                "SELECT set_config('app.clerk_user_id', $1, true);",
                ctx.company_a.owner.clerk_user_id,
            )
            insert_failed = False
            try:
                await conn.execute(
                    """
                    INSERT INTO public.memberships (id, tenant_id, user_id, role_id, status)
                    VALUES ($1, $2, $3, $4, 'active');
                """,
                    uuid.uuid4(),
                    uuid.UUID(ctx.company_b.tenant_id),
                    uuid.UUID(ctx.company_a.employee.user_id),
                    role_id,
                )
            except (
                asyncpg.exceptions.InsufficientPrivilegeError,
                asyncpg.exceptions.CheckViolationError,
            ):
                insert_failed = True
            assert insert_failed, "CRITICAL: Cross-tenant membership INSERT was permitted by RLS!"

        # 4b. Attempt to insert into tenants table must fail
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                "SELECT set_config('app.clerk_user_id', $1, true);",
                ctx.company_a.owner.clerk_user_id,
            )
            tenant_insert_failed = False
            try:
                await conn.execute(
                    """
                    INSERT INTO public.tenants (id, name, clerk_org_id, status)
                    VALUES ($1, 'Rogue Tenant', 'org_rogue_99', 'active');
                """,
                    uuid.uuid4(),
                )
            except (
                asyncpg.exceptions.InsufficientPrivilegeError,
                asyncpg.exceptions.CheckViolationError,
            ):
                tenant_insert_failed = True
            assert tenant_insert_failed, (
                "CRITICAL: Tenant INSERT was permitted for agentic_hr_app role!"
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_negative_05_uncontexted_tenant_access_blocked(
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 5 (Database): Uncontexted connection returns no tenant records."""
    conn = await get_app_connection()
    try:
        # No set_config executed; connection is uncontexted
        tenants = await conn.fetch("SELECT * FROM public.tenants;")
        assert len(tenants) == 0, f"Uncontexted query returned tenants: {tenants}"

        memberships = await conn.fetch("SELECT * FROM public.memberships;")
        assert len(memberships) == 0, f"Uncontexted query returned memberships: {memberships}"

        users = await conn.fetch("SELECT * FROM public.users;")
        assert len(users) == 0, f"Uncontexted query returned users: {users}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_negative_06_non_member_cannot_read_tenant_memberships(
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 6 (Database): Non-member cannot read tenant records."""
    ctx = isolation_context
    conn = await get_app_connection()
    try:
        async with conn.transaction():
            # Company A context, but queried for a user with NO membership in Company A
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                "SELECT set_config('app.clerk_user_id', $1, true);",
                ctx.company_a.owner.clerk_user_id,
            )

            # Query for Globex Owner's membership inside Company A returns 0 rows
            rows = await conn.fetch(
                "SELECT * FROM public.memberships WHERE user_id = $1::uuid;",
                ctx.company_b.owner.user_id,
            )
            assert len(rows) == 0, f"Foreign user membership leaked: {rows}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_negative_09_missing_or_empty_context_fails_closed(
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 9: Missing, empty, or unseeded tenant context fails closed."""
    conn = await get_app_connection()
    try:
        # Case A: Context explicitly set to empty string
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', '', true);")
            rows = await conn.fetch("SELECT * FROM public.memberships;")
            assert len(rows) == 0, "Empty context leaked memberships!"

        # Case B: Context set to random unseeded UUID
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true);", str(uuid.uuid4()))
            rows = await conn.fetch("SELECT * FROM public.memberships;")
            assert len(rows) == 0, "Random UUID context returned memberships!"
            tenant_rows = await conn.fetch("SELECT * FROM public.tenants;")
            assert len(tenant_rows) == 0, "Random UUID context returned tenants!"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_negative_10_connection_pooling_stress_leak_prevention(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    NEGATIVE 10: Database connection pooling does not leak tenant context across checkouts.
    Simulates high-concurrency interleaved checkout loop (50 checkouts across 10 workers).
    """
    ctx = isolation_context
    pool = await asyncpg.create_pool(APP_DB_URL, min_size=3, max_size=10)
    assert pool is not None

    async def worker(worker_id: int) -> None:
        for _ in range(5):
            op_choice = random.choice(["company_a", "company_b", "uncontexted", "rollback_a"])

            if op_choice == "company_a":
                async with pool.acquire() as conn, conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
                    )
                    count = await conn.fetchval("SELECT count(*) FROM public.memberships;")
                    assert count > 0, "Company A should see its memberships"

            elif op_choice == "company_b":
                async with pool.acquire() as conn, conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', $1, true);", ctx.company_b.tenant_id
                    )
                    count = await conn.fetchval("SELECT count(*) FROM public.memberships;")
                    assert count > 0, "Company B should see its memberships"

            elif op_choice == "uncontexted":
                async with pool.acquire() as conn:
                    # Connection checked out without set_config MUST have empty context
                    val = await conn.fetchval(
                        "SELECT NULLIF(current_setting('app.tenant_id', true), '');"
                    )
                    assert val is None, (
                        f"CRITICAL: Leaked app.tenant_id '{val}' on uncontexted pool checkout!"
                    )
                    count = await conn.fetchval("SELECT count(*) FROM public.memberships;")
                    assert count == 0, (
                        f"CRITICAL: Uncontexted pool checkout saw {count} memberships!"
                    )

            elif op_choice == "rollback_a":
                async with pool.acquire() as conn:
                    try:
                        async with conn.transaction():
                            await conn.execute(
                                "SELECT set_config('app.tenant_id', $1, true);",
                                ctx.company_a.tenant_id,
                            )
                            # Intentionally abort transaction
                            raise ValueError("Forced test abort")
                    except ValueError:
                        pass
                    # After rollback, verify connection context is clean
                    val = await conn.fetchval(
                        "SELECT NULLIF(current_setting('app.tenant_id', true), '');"
                    )
                    assert val is None, (
                        f"CRITICAL: Post-rollback connection leaked app.tenant_id '{val}'!"
                    )

    try:
        tasks = [worker(i) for i in range(10)]
        await asyncio.gather(*tasks)
    finally:
        await pool.close()


# ===========================================================================
# 2. FASTAPI APPLICATION AUTHORIZATION & ROUTE ISOLATION TESTS
# ===========================================================================


@pytest.mark.asyncio
async def test_api_negative_01_company_a_cannot_read_company_b_records(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 1 (API): Company A user querying /auth/me sees only Company A data."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    resp = await async_client.get("/api/v1/auth/me", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == ctx.company_a.tenant_id
    assert data["clerk_org_id"] == ctx.company_a.clerk_org_id
    assert data["tenant_name"] == ctx.company_a.name
    assert data["role"] == "owner"


@pytest.mark.asyncio
async def test_api_negative_02_company_a_cannot_update_company_b_records(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 2 (API): Company A Owner cannot update Company B memberships."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Attempt to update Company B manager's membership
    url = f"/api/v1/auth/memberships/{ctx.company_b.manager.membership_id}"
    payload = {"role_key": "employee", "is_hr_head": False}
    resp = await async_client.patch(url, headers=headers, json=payload)

    # Must be 404 Not Found (row is invisible to Company A context under RLS)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_api_negative_05_user_without_active_tenant_blocked(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 5 (API): User without active organization claim is rejected."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.orphan_token_no_org}"}
    resp = await async_client.get("/api/v1/auth/me", headers=headers)

    assert resp.status_code == 403
    assert "active organization" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_api_negative_06_user_without_membership_blocked(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 6 (API): User attempting to access organization where they have no membership is rejected."""
    ctx = isolation_context
    # Company A Employee attempts to access Company B organization
    from tests.isolation_harness import create_isolation_token

    rogue_token = create_isolation_token(
        sub=ctx.company_a.employee.clerk_user_id,
        org_id=ctx.company_b.clerk_org_id,
    )
    headers = {"Authorization": f"Bearer {rogue_token}"}
    resp = await async_client.get("/api/v1/auth/me", headers=headers)

    assert resp.status_code == 403
    assert "is not a member" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_api_negative_07_parameter_tampering_ignored(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 7 (API): Request parameter / header tampering cannot change tenant context."""
    ctx = isolation_context
    # Company A user attempts to pass Company B's tenant_id via query param and header
    headers = {
        "Authorization": f"Bearer {ctx.company_a.owner.token}",
        "X-Tenant-ID": ctx.company_b.tenant_id,
    }
    params = {"tenant_id": ctx.company_b.tenant_id}
    resp = await async_client.get("/api/v1/auth/me", headers=headers, params=params)

    assert resp.status_code == 200
    data = resp.json()
    # Verified tenant_id must strictly match the JWT's Company A, ignoring the injected Company B
    assert data["tenant_id"] == ctx.company_a.tenant_id
    assert data["clerk_org_id"] == ctx.company_a.clerk_org_id


@pytest.mark.asyncio
async def test_api_negative_08_organization_switching_security(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """NEGATIVE 8 (API): Multi-company user cleanly switches contexts without cross-leakage."""
    ctx = isolation_context

    # Step 1: User accesses Company A as employee
    headers_a = {"Authorization": f"Bearer {ctx.cross_company_token_a}"}
    resp_a = await async_client.get("/api/v1/auth/me", headers=headers_a)
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["tenant_id"] == ctx.company_a.tenant_id
    assert data_a["role"] == "employee"

    # Step 2: Attempting to present Company A token while claiming Company B in headers does NOT switch org
    headers_tamper = {
        "Authorization": f"Bearer {ctx.cross_company_token_a}",
        "X-Tenant-ID": ctx.company_b.clerk_org_id,
    }
    resp_tamper = await async_client.get("/api/v1/auth/me", headers=headers_tamper)
    assert resp_tamper.status_code == 200
    assert resp_tamper.json()["tenant_id"] == ctx.company_a.tenant_id

    # Step 3: User presents Company B token -> cleanly switches to Company B as manager
    headers_b = {"Authorization": f"Bearer {ctx.cross_company_token_b}"}
    resp_b = await async_client.get("/api/v1/auth/me", headers=headers_b)
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["tenant_id"] == ctx.company_b.tenant_id
    assert data_b["role"] == "manager"


# ===========================================================================
# 3. ROLE & MEMBERSHIP RBAC ISOLATION TESTS
# ===========================================================================


@pytest.mark.asyncio
async def test_role_rbac_owner_vs_manager_vs_employee_permissions(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that only Owners can update memberships; Managers and Employees are denied (403)."""
    ctx = isolation_context
    target_membership_id = ctx.company_a.employee.membership_id
    url = f"/api/v1/auth/memberships/{target_membership_id}"
    payload = {"is_hr_head": True}

    # 1. Employee attempting update -> 403 Forbidden
    headers_emp = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    resp_emp = await async_client.patch(url, headers=headers_emp, json=payload)
    assert resp_emp.status_code == 403

    # 2. Manager attempting update -> 403 Forbidden
    headers_mgr = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}
    resp_mgr = await async_client.patch(url, headers=headers_mgr, json=payload)
    assert resp_mgr.status_code == 403

    # 3. Owner attempting update on fellow member -> 200 OK
    headers_owner = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    resp_owner = await async_client.patch(url, headers=headers_owner, json=payload)
    assert resp_owner.status_code == 200
    assert resp_owner.json()["is_hr_head"] is True


@pytest.mark.asyncio
async def test_role_rbac_owner_anti_self_promotion(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies Owner cannot modify their own role or permission flags (anti-self-promotion)."""
    ctx = isolation_context
    owner_mem_id = ctx.company_a.owner.membership_id
    url = f"/api/v1/auth/memberships/{owner_mem_id}"
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    resp = await async_client.patch(url, headers=headers, json={"role_key": "employee"})
    assert resp.status_code == 400
    assert "alter your own membership" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_role_rbac_cannot_assign_nonexistent_role(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies assigning an invalid role key returns HTTP 400 Bad Request."""
    ctx = isolation_context
    target_mem_id = ctx.company_a.employee.membership_id
    url = f"/api/v1/auth/memberships/{target_mem_id}"
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    resp = await async_client.patch(url, headers=headers, json={"role_key": "super_root_admin"})
    assert resp.status_code == 400
    assert "Invalid role key" in resp.json()["error"]["message"]


# ===========================================================================
# 4. REUSABLE FUTURE-PROOF ISOLATION HARNESS VERIFICATION
# ===========================================================================


@pytest.mark.asyncio
async def test_reusable_harness_assert_table_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that assert_table_tenant_isolation correctly validates multi-tenant isolation
    on the memberships table, proving the harness is ready for all future stages.
    """
    ctx = isolation_context

    def membership_insert_factory(target_tenant_id: str) -> tuple[str, list]:
        sql = """
            INSERT INTO public.memberships (id, tenant_id, user_id, role_id, status)
            VALUES ($1, $2::uuid, $3::uuid, (SELECT id FROM public.roles WHERE role_key = 'employee'), 'active');
        """
        return (sql, [uuid.uuid4(), target_tenant_id, ctx.company_a.employee.user_id])

    await assert_table_tenant_isolation(
        table_name="memberships",
        company_a_tenant_id=ctx.company_a.tenant_id,
        company_b_tenant_id=ctx.company_b.tenant_id,
        company_a_row_id=ctx.company_a.owner.membership_id,
        company_b_row_id=ctx.company_b.owner.membership_id,
        insert_sql_factory=membership_insert_factory,
    )


@pytest.mark.asyncio
async def test_reusable_harness_assert_route_tenant_isolation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that assert_route_tenant_isolation validates protected endpoints against cross-tenant access.
    """
    ctx = isolation_context
    url_a = f"/api/v1/auth/memberships/{ctx.company_a.employee.membership_id}"
    url_b = f"/api/v1/auth/memberships/{ctx.company_b.employee.membership_id}"

    # Company A Owner vs Company B Owner
    await assert_route_tenant_isolation(
        client=async_client,
        method="PATCH",
        url_company_a=url_a,
        url_company_b=url_b,
        company_a_token=ctx.company_a.owner.token,
        company_b_token=ctx.company_b.owner.token,
        expected_status_on_own=200,
        expected_status_on_cross=404,
        json_payload={"is_hr_head": False},
    )


# ===========================================================================
# 5. STAGE 3 CONNECTOR & SHEETS TABLES MULTI-TENANT ISOLATION TESTS
# ===========================================================================


@pytest.mark.asyncio
async def test_stage3_connector_credentials_multi_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that connector_credentials enforces multi-tenant boundaries via RLS."""
    ctx = isolation_context
    conn = await get_app_connection()

    cred_a_id = uuid.uuid4()
    cred_b_id = uuid.uuid4()

    enc_a = encrypt_field(
        "mock_tok_a",
        EncryptionContext(
            ctx.company_a.tenant_id, "connector_credential", cred_a_id, "access_token"
        ),
    )
    enc_b = encrypt_field(
        "mock_tok_b",
        EncryptionContext(
            ctx.company_b.tenant_id, "connector_credential", cred_b_id, "access_token"
        ),
    )

    try:
        # Seed Company A
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.connector_credentials (
                    id, tenant_id, provider, account_email, scopes,
                    encrypted_access_token, encrypted_refresh_token, status
                ) VALUES ($1, $2::uuid, 'google', $3, '["gmail.send"]'::jsonb, $4, NULL, 'connected');
                """,
                cred_a_id,
                ctx.company_a.tenant_id,
                f"owner_a_{uuid.uuid4().hex[:6]}@gmail.com",
                enc_a,
            )

        # Seed Company B
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_b.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.connector_credentials (
                    id, tenant_id, provider, account_email, scopes,
                    encrypted_access_token, encrypted_refresh_token, status
                ) VALUES ($1, $2::uuid, 'google', $3, '["gmail.send"]'::jsonb, $4, NULL, 'connected');
                """,
                cred_b_id,
                ctx.company_b.tenant_id,
                f"owner_b_{uuid.uuid4().hex[:6]}@gmail.com",
                enc_b,
            )

        def insert_factory(target_tenant_id: str) -> tuple[str, list]:
            ins_id = uuid.uuid4()
            ins_enc = encrypt_field(
                "ins_tok",
                EncryptionContext(target_tenant_id, "connector_credential", ins_id, "access_token"),
            )
            sql = """
                INSERT INTO public.connector_credentials (
                    id, tenant_id, provider, account_email, scopes,
                    encrypted_access_token, encrypted_refresh_token, status
                ) VALUES ($1, $2::uuid, 'google', $3, '["gmail.send"]'::jsonb, $4, NULL, 'connected');
            """
            return (
                sql,
                [ins_id, target_tenant_id, f"test_{uuid.uuid4().hex[:6]}@gmail.com", ins_enc],
            )

        await assert_table_tenant_isolation(
            table_name="connector_credentials",
            company_a_tenant_id=ctx.company_a.tenant_id,
            company_b_tenant_id=ctx.company_b.tenant_id,
            company_a_row_id=str(cred_a_id),
            company_b_row_id=str(cred_b_id),
            insert_sql_factory=insert_factory,
        )
    finally:
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM public.connector_credentials WHERE id IN ($1::uuid, $2::uuid);",
                    cred_a_id,
                    cred_b_id,
                )
        except Exception:  # noqa: S110
            pass
        await conn.close()


@pytest.mark.asyncio
async def test_stage3_sent_messages_multi_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that sent_messages enforces multi-tenant boundaries via RLS."""
    ctx = isolation_context
    conn = await get_app_connection()

    msg_a_id = uuid.uuid4()
    msg_b_id = uuid.uuid4()

    try:
        # Seed Company A
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.sent_messages (
                    id, tenant_id, recipient_email, subject, text_body, status
                ) VALUES ($1, $2::uuid, 'candidate_a@test.com', 'Interview A', 'Details A', 'sent');
                """,
                msg_a_id,
                ctx.company_a.tenant_id,
            )

        # Seed Company B
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_b.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.sent_messages (
                    id, tenant_id, recipient_email, subject, text_body, status
                ) VALUES ($1, $2::uuid, 'candidate_b@test.com', 'Interview B', 'Details B', 'sent');
                """,
                msg_b_id,
                ctx.company_b.tenant_id,
            )

        def insert_factory(target_tenant_id: str) -> tuple[str, list]:
            ins_id = uuid.uuid4()
            sql = """
                INSERT INTO public.sent_messages (
                    id, tenant_id, recipient_email, subject, text_body, status
                ) VALUES ($1, $2::uuid, 'candidate_ins@test.com', 'Subject Ins', 'Text Ins', 'sent');
            """
            return (sql, [ins_id, target_tenant_id])

        await assert_table_tenant_isolation(
            table_name="sent_messages",
            company_a_tenant_id=ctx.company_a.tenant_id,
            company_b_tenant_id=ctx.company_b.tenant_id,
            company_a_row_id=str(msg_a_id),
            company_b_row_id=str(msg_b_id),
            insert_sql_factory=insert_factory,
        )
    finally:
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM public.sent_messages WHERE id IN ($1::uuid, $2::uuid);",
                    msg_a_id,
                    msg_b_id,
                )
        except Exception:  # noqa: S110
            pass
        await conn.close()


@pytest.mark.asyncio
async def test_stage3_connector_route_tenant_isolation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that PATCH /api/v1/connectors/{id}/read-only enforces route-level tenant isolation."""
    ctx = isolation_context

    # 1. Company A Owner creates credential in Company A
    resp_a = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
        json={
            "provider": "google",
            "account_email": f"owner.comp_a_{uuid.uuid4().hex[:6]}@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.comp_a_token",
        },
    )
    assert resp_a.status_code == 200
    cred_a_id = resp_a.json()["id"]

    # 2. Company B Owner creates credential in Company B
    resp_b = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers={"Authorization": f"Bearer {ctx.company_b.owner.token}"},
        json={
            "provider": "google",
            "account_email": f"owner.comp_b_{uuid.uuid4().hex[:6]}@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.comp_b_token",
        },
    )
    assert resp_b.status_code == 200
    cred_b_id = resp_b.json()["id"]

    url_a = f"/api/v1/connectors/{cred_a_id}/read-only"
    url_b = f"/api/v1/connectors/{cred_b_id}/read-only"

    # 3. Assert Route Tenant Isolation: Company A Owner calling Company B credential returns 404
    try:
        await assert_route_tenant_isolation(
            client=async_client,
            method="PATCH",
            url_company_a=url_a,
            url_company_b=url_b,
            company_a_token=ctx.company_a.owner.token,
            company_b_token=ctx.company_b.owner.token,
            expected_status_on_own=200,
            expected_status_on_cross=404,
            json_payload={"is_read_only": True},
        )
    finally:
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM public.connector_credentials WHERE id IN ($1::uuid, $2::uuid);",
                    uuid.UUID(cred_a_id),
                    uuid.UUID(cred_b_id),
                )
        except Exception:  # noqa: S110
            pass
        await conn.close()


@pytest.mark.asyncio
async def test_stage3_sheets_sync_queue_multi_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that sheets_sync_queue enforces PostgreSQL RLS across Company A and Company B."""
    ctx = isolation_context
    item_a_id = uuid.uuid4()
    item_b_id = uuid.uuid4()

    conn = await get_app_connection()
    try:
        # Seed Company A
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.sheets_sync_queue (
                    id, tenant_id, spreadsheet_id, sheet_name, direction, entity_type, entity_id, action, payload, status
                ) VALUES ($1, $2::uuid, 'sheet_a', 'Employees', 'postgres_to_sheets', 'employee', 'EMP-01', 'upsert', '{"name": "A"}'::jsonb, 'pending');
                """,
                item_a_id,
                ctx.company_a.tenant_id,
            )

        # Seed Company B
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_b.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.sheets_sync_queue (
                    id, tenant_id, spreadsheet_id, sheet_name, direction, entity_type, entity_id, action, payload, status
                ) VALUES ($1, $2::uuid, 'sheet_b', 'Employees', 'postgres_to_sheets', 'employee', 'EMP-02', 'upsert', '{"name": "B"}'::jsonb, 'pending');
                """,
                item_b_id,
                ctx.company_b.tenant_id,
            )

        def insert_factory(target_tenant_id: str) -> tuple[str, list]:
            ins_id = uuid.uuid4()
            sql = """
                INSERT INTO public.sheets_sync_queue (
                    id, tenant_id, spreadsheet_id, sheet_name, direction, entity_type, entity_id, action, payload, status
                ) VALUES ($1, $2::uuid, 'sheet_ins', 'Employees', 'postgres_to_sheets', 'employee', 'EMP-INS', 'upsert', '{"name": "Ins"}'::jsonb, 'pending');
            """
            return (sql, [ins_id, target_tenant_id])

        await assert_table_tenant_isolation(
            table_name="sheets_sync_queue",
            company_a_tenant_id=ctx.company_a.tenant_id,
            company_b_tenant_id=ctx.company_b.tenant_id,
            company_a_row_id=str(item_a_id),
            company_b_row_id=str(item_b_id),
            insert_sql_factory=insert_factory,
        )
    finally:
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM public.sheets_sync_queue WHERE id IN ($1::uuid, $2::uuid);",
                    item_a_id,
                    item_b_id,
                )
        except Exception:  # noqa: S110
            pass
        await conn.close()


@pytest.mark.asyncio
async def test_stage3_sheets_drift_logs_multi_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that sheets_drift_logs enforces PostgreSQL RLS across Company A and Company B."""
    ctx = isolation_context
    drift_a_id = uuid.uuid4()
    drift_b_id = uuid.uuid4()

    conn = await get_app_connection()
    try:
        # Seed Company A
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.sheets_drift_logs (
                    id, tenant_id, spreadsheet_id, sheet_name, entity_type, entity_id, field_name, postgres_value, sheets_value, resolution
                ) VALUES ($1, $2::uuid, 'sheet_a', 'Employees', 'employee', 'EMP-01', 'role', 'Staff', 'Lead', 'postgres_overwrote_sheets');
                """,
                drift_a_id,
                ctx.company_a.tenant_id,
            )

        # Seed Company B
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_b.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.sheets_drift_logs (
                    id, tenant_id, spreadsheet_id, sheet_name, entity_type, entity_id, field_name, postgres_value, sheets_value, resolution
                ) VALUES ($1, $2::uuid, 'sheet_b', 'Employees', 'employee', 'EMP-02', 'role', 'Dev', 'VP', 'postgres_overwrote_sheets');
                """,
                drift_b_id,
                ctx.company_b.tenant_id,
            )

        def insert_factory(target_tenant_id: str) -> tuple[str, list]:
            ins_id = uuid.uuid4()
            sql = """
                INSERT INTO public.sheets_drift_logs (
                    id, tenant_id, spreadsheet_id, sheet_name, entity_type, entity_id, field_name, postgres_value, sheets_value, resolution
                ) VALUES ($1, $2::uuid, 'sheet_ins', 'Employees', 'employee', 'EMP-INS', 'role', 'Eng', 'Manager', 'postgres_overwrote_sheets');
            """
            return (sql, [ins_id, target_tenant_id])

        await assert_table_tenant_isolation(
            table_name="sheets_drift_logs",
            company_a_tenant_id=ctx.company_a.tenant_id,
            company_b_tenant_id=ctx.company_b.tenant_id,
            company_a_row_id=str(drift_a_id),
            company_b_row_id=str(drift_b_id),
            insert_sql_factory=insert_factory,
        )
    finally:
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM public.sheets_drift_logs WHERE id IN ($1::uuid, $2::uuid);",
                    drift_a_id,
                    drift_b_id,
                )
        except Exception:  # noqa: S110
            pass
        await conn.close()


@pytest.mark.asyncio
async def test_stage3_drive_files_multi_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that Company A cannot read, update, or delete Company B's Drive file metadata via RLS."""
    ctx = isolation_context
    file_a_id = uuid.uuid4()
    file_b_id = uuid.uuid4()

    conn = await get_app_connection()
    try:
        # Seed Company A
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.drive_files (
                    id, tenant_id, drive_file_id, folder_id, name, mime_type, size_bytes, web_view_link
                ) VALUES ($1, $2::uuid, 'goog_file_a', 'folder_a', 'resume_a.pdf', 'application/pdf', 1024, 'https://drive.google.com/file/a');
                """,
                file_a_id,
                ctx.company_a.tenant_id,
            )

        # Seed Company B
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_b.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.drive_files (
                    id, tenant_id, drive_file_id, folder_id, name, mime_type, size_bytes, web_view_link
                ) VALUES ($1, $2::uuid, 'goog_file_b', 'folder_b', 'resume_b.pdf', 'application/pdf', 2048, 'https://drive.google.com/file/b');
                """,
                file_b_id,
                ctx.company_b.tenant_id,
            )

        def insert_factory(target_tenant_id: str) -> tuple[str, list]:
            ins_id = uuid.uuid4()
            sql = """
                INSERT INTO public.drive_files (
                    id, tenant_id, drive_file_id, folder_id, name, mime_type, size_bytes, web_view_link
                ) VALUES ($1, $2::uuid, 'goog_file_ins', 'folder_ins', 'resume_ins.pdf', 'application/pdf', 512, 'https://drive.google.com/file/ins');
            """
            return (sql, [ins_id, target_tenant_id])

        await assert_table_tenant_isolation(
            table_name="drive_files",
            company_a_tenant_id=ctx.company_a.tenant_id,
            company_b_tenant_id=ctx.company_b.tenant_id,
            company_a_row_id=str(file_a_id),
            company_b_row_id=str(file_b_id),
            insert_sql_factory=insert_factory,
        )
    finally:
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM public.drive_files WHERE id IN ($1::uuid, $2::uuid);",
                    file_a_id,
                    file_b_id,
                )
        except Exception:  # noqa: S110
            pass
        await conn.close()


@pytest.mark.asyncio
async def test_stage3_calendar_events_multi_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that Company A cannot read, update, or delete Company B's Calendar events via RLS."""
    ctx = isolation_context
    event_a_id = uuid.uuid4()
    event_b_id = uuid.uuid4()

    conn = await get_app_connection()
    try:
        # Seed Company A
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.calendar_events (
                    id, tenant_id, google_event_id, title, start_time, end_time, timezone, attendees, status
                ) VALUES ($1, $2::uuid, 'cal_event_a', 'Interview A', '2026-11-12 10:00:00+05', '2026-11-12 11:00:00+05', 'Asia/Karachi', '["candidate_a@test.com"]'::jsonb, 'confirmed');
                """,
                event_a_id,
                ctx.company_a.tenant_id,
            )

        # Seed Company B
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_b.tenant_id
            )
            await conn.execute(
                """
                INSERT INTO public.calendar_events (
                    id, tenant_id, google_event_id, title, start_time, end_time, timezone, attendees, status
                ) VALUES ($1, $2::uuid, 'cal_event_b', 'Interview B', '2026-11-12 14:00:00+05', '2026-11-12 15:00:00+05', 'Asia/Karachi', '["candidate_b@test.com"]'::jsonb, 'confirmed');
                """,
                event_b_id,
                ctx.company_b.tenant_id,
            )

        def insert_factory(target_tenant_id: str) -> tuple[str, list]:
            ins_id = uuid.uuid4()
            sql = """
                INSERT INTO public.calendar_events (
                    id, tenant_id, google_event_id, title, start_time, end_time, timezone, attendees, status
                ) VALUES ($1, $2::uuid, 'cal_event_ins', 'Interview INS', '2026-11-12 16:00:00+05', '2026-11-12 17:00:00+05', 'Asia/Karachi', '["candidate_ins@test.com"]'::jsonb, 'confirmed');
            """
            return (sql, [ins_id, target_tenant_id])

        await assert_table_tenant_isolation(
            table_name="calendar_events",
            company_a_tenant_id=ctx.company_a.tenant_id,
            company_b_tenant_id=ctx.company_b.tenant_id,
            company_a_row_id=str(event_a_id),
            company_b_row_id=str(event_b_id),
            insert_sql_factory=insert_factory,
        )
    finally:
        try:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM public.calendar_events WHERE id IN ($1::uuid, $2::uuid);",
                    event_a_id,
                    event_b_id,
                )
        except Exception:  # noqa: S110
            pass
        await conn.close()
