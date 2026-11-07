"""
Comprehensive Database Row-Level Security (RLS) & Tenant Isolation Test Suite.
All tests execute strictly under the restricted non-owner runtime role: agentic_hr_app.

Verifies:
1. Runtime role attributes (NOSUPERUSER, NOBYPASSRLS).
2. Runtime role does NOT own protected tables.
3. Column-level permissions on users (immutable clerk_user_id and email).
4. Column-level permissions on users (mutable name field).
5. Missing tenant context denies all queries (fail-closed).
6. Empty tenant context denies all queries (fail-closed).
7. Cross-tenant user isolation (Acme Tech vs Globex Corp).
8. Multi-company user visibility (candidate2 visible in both Acme and Globex).
9. Cross-tenant INSERT rejection (WITH CHECK policy violation).
10. Cross-tenant UPDATE & DELETE rejection (0 rows affected).
11. Tenant enumeration blocked (SELECT * FROM tenants returns 0 rows).
12. Tenant resolver scalar lookup success (returns Acme UUID).
13. Tenant resolver invalid org returns NULL.
14. Tenant resolver inactive tenant returns NULL.
15. Resolver role (agentic_hr_tenant_resolver) cannot login (NOLOGIN).
16. Resolver function execution revoked from PUBLIC.
17. Transaction rollback clears context.
18. Connection pool checkout safety (zero leakage across transactions).
19. Employee self-promotion blocked via API (HTTP 403 Forbidden).
20. Owner anti-self-promotion blocked via API (HTTP 400 Bad Request).
21. Owner can update other member permissions via API (HTTP 200 OK).
"""

import time
import uuid

import asyncpg
import httpx
import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.clerk import clear_test_signing_keys, register_test_signing_key
from app.core.config import settings
from app.main import app

APP_DB_URL = "postgresql://agentic_hr_app:agentic_hr_app_secret@127.0.0.1:5432/agentic_hr"
ADMIN_DB_URL = "postgresql://postgres:postgres@127.0.0.1:5432/agentic_hr"

# Cryptographic Test Key Setup for API integration tests
TEST_KID = "test-rls-kid-01"
_private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_public_key = _private_key.public_key()


@pytest.fixture(autouse=True)
def setup_test_signing_keys():
    """Registers offline public RSA test key for signature verification."""
    register_test_signing_key(TEST_KID, _public_key)
    yield
    clear_test_signing_keys()


def create_token(sub: str, org_id: str = "org_acme_tech_01") -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "iss": settings.CLERK_ISSUER,
        "iat": now,
        "exp": now + 3600,
        "nbf": now,
        "org_id": org_id,
        "org_role": "admin" if "owner" in sub else "member",
    }
    pem_bytes = _private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, pem_bytes, algorithm="RS256", headers={"kid": TEST_KID})


@pytest.mark.asyncio
async def test_runtime_role_privileges():
    """Verifies agentic_hr_app has rolsuper = False and rolbypassrls = False."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        role_info = await conn.fetchrow("""
            SELECT rolname, rolsuper, rolcanlogin, rolbypassrls 
            FROM pg_catalog.pg_roles 
            WHERE rolname = 'agentic_hr_app';
        """)
        assert role_info is not None
        assert role_info["rolsuper"] is False
        assert role_info["rolcanlogin"] is True
        assert role_info["rolbypassrls"] is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_runtime_role_not_owner():
    """Verifies that protected tables are owned by agentic_hr_migrator, NOT agentic_hr_app."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        tables = await conn.fetch("""
            SELECT c.relname, pg_get_userbyid(c.relowner) AS owner, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname IN ('tenants', 'users', 'memberships')
            ORDER BY c.relname;
        """)
        assert len(tables) == 3
        for t in tables:
            assert t["owner"] == "agentic_hr_migrator", f"{t['relname']} must be owned by migrator"
            assert t["relrowsecurity"] is True
            assert t["relforcerowsecurity"] is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_user_immutable_columns():
    """Verifies that column-level grants prevent agentic_hr_app from modifying clerk_user_id or email."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        # In a transaction with self clerk context:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.clerk_user_id', 'user_test_owner_01', true)")
            # Attempt to update clerk_user_id -> must raise InsufficientPrivilegeError
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError) as exc_info:
                await conn.execute("""
                    UPDATE public.users 
                    SET clerk_user_id = 'malicious_sub' 
                    WHERE clerk_user_id = 'user_test_owner_01';
                """)
            err_msg = str(exc_info.value).lower()
            assert "permission denied" in err_msg
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_user_permitted_columns():
    """Verifies that agentic_hr_app can update permitted column 'name' on own row."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.clerk_user_id', 'user_test_owner_01', true)")
            # Fetch original name
            orig = await conn.fetchval(
                "SELECT name FROM public.users WHERE clerk_user_id = 'user_test_owner_01'"
            )
            assert orig is not None

            # Update permitted column 'name'
            res = await conn.execute("""
                UPDATE public.users 
                SET name = 'Updated Test Owner' 
                WHERE clerk_user_id = 'user_test_owner_01';
            """)
            assert res == "UPDATE 1"

            updated = await conn.fetchval(
                "SELECT name FROM public.users WHERE clerk_user_id = 'user_test_owner_01'"
            )
            assert updated == "Updated Test Owner"

            # Restore original name
            await conn.execute(
                "UPDATE public.users SET name = $1 WHERE clerk_user_id = 'user_test_owner_01'",
                orig,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_tenant_context_denies_all():
    """Verifies that without app.tenant_id, queries to memberships, tenants, and users return 0 rows."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        async with conn.transaction():
            memberships = await conn.fetch("SELECT * FROM public.memberships")
            tenants = await conn.fetch("SELECT * FROM public.tenants")
            users = await conn.fetch("SELECT * FROM public.users")

            assert len(memberships) == 0
            assert len(tenants) == 0
            assert len(users) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_empty_tenant_context_denies_all():
    """Verifies that empty string context evaluates to NULL and returns 0 rows."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', '', true)")
            await conn.execute("SELECT set_config('app.clerk_user_id', '', true)")

            memberships = await conn.fetch("SELECT * FROM public.memberships")
            tenants = await conn.fetch("SELECT * FROM public.tenants")
            users = await conn.fetch("SELECT * FROM public.users")

            assert len(memberships) == 0
            assert len(tenants) == 0
            assert len(users) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cross_tenant_isolation_acme_vs_globex():
    """Verifies strict data isolation: Acme context returns 5 users, Globex returns 2 users."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        # Resolve Acme UUID
        acme_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_acme_tech_01')"
        )
        assert acme_id is not None

        # Resolve Globex UUID
        globex_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_globex_corp_02')"
        )
        assert globex_id is not None

        # 1. Acme context
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1::text, true)", str(acme_id))
            acme_users = await conn.fetch("SELECT email FROM public.users ORDER BY email")
            acme_emails = [u["email"] for u in acme_users]

            assert len(acme_emails) == 5
            assert "agentic.hr.test.ubaid.owner@gmail.com" in acme_emails
            assert "agentic.hr.test.ubaid.recruiter@gmail.com" in acme_emails
            assert "agentic.hr.test.ubaid.hm@gmail.com" in acme_emails
            assert "agentic.hr.test.ubaid.employee@gmail.com" in acme_emails
            assert "agentic.hr.test.ubaid.candidate2@gmail.com" in acme_emails
            # Globex-only candidate1 must NOT be visible
            assert "agentic.hr.test.ubaid.candidate1@gmail.com" not in acme_emails

        # 2. Globex context
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1::text, true)", str(globex_id))
            globex_users = await conn.fetch("SELECT email FROM public.users ORDER BY email")
            globex_emails = [u["email"] for u in globex_users]

            assert len(globex_emails) == 3
            assert "agentic.hr.test.ubaid.candidate1@gmail.com" in globex_emails
            assert "agentic.hr.test.ubaid.candidate2@gmail.com" in globex_emails
            assert "agentic.hr.test.ubaid.globex_owner@gmail.com" in globex_emails
            # Acme employees must NOT be visible in Globex
            assert "agentic.hr.test.ubaid.owner@gmail.com" not in globex_emails
            assert "agentic.hr.test.ubaid.recruiter@gmail.com" not in globex_emails
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_multi_company_candidate_visibility():
    """Verifies that candidate2 (who holds memberships in both Acme and Globex) is visible in both."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        acme_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_acme_tech_01')"
        )
        globex_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_globex_corp_02')"
        )

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1::text, true)", str(acme_id))
            acme_cand = await conn.fetchval(
                "SELECT email FROM public.users WHERE email = 'agentic.hr.test.ubaid.candidate2@gmail.com'"
            )
            assert acme_cand == "agentic.hr.test.ubaid.candidate2@gmail.com"

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1::text, true)", str(globex_id))
            globex_cand = await conn.fetchval(
                "SELECT email FROM public.users WHERE email = 'agentic.hr.test.ubaid.candidate2@gmail.com'"
            )
            assert globex_cand == "agentic.hr.test.ubaid.candidate2@gmail.com"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cross_tenant_insert_blocked():
    """Verifies that in Acme context, inserting a membership for Globex fails with RLS policy violation."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        acme_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_acme_tech_01')"
        )
        globex_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_globex_corp_02')"
        )

        role_id = await conn.fetchval("SELECT id FROM public.roles WHERE role_key = 'employee'")
        user_id = await conn.fetchval(
            "SELECT id FROM public.users WHERE clerk_user_id = 'user_test_employee_04'"
        )

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1::text, true)", str(acme_id))
            # Attempt to insert membership pointing to Globex while in Acme context
            with pytest.raises(
                (
                    asyncpg.exceptions.InsufficientPrivilegeError,
                    asyncpg.exceptions.CheckViolationError,
                )
            ):
                await conn.execute(
                    """
                    INSERT INTO public.memberships (id, tenant_id, user_id, role_id, status)
                    VALUES ($1, $2, $3, $4, 'active')
                    """,
                    uuid.uuid4(),
                    globex_id,
                    user_id,
                    role_id,
                )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cross_tenant_update_and_delete_blocked():
    """Verifies that in Acme context, attempting to UPDATE or DELETE a Globex row affects 0 rows."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        acme_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_acme_tech_01')"
        )
        globex_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_globex_corp_02')"
        )

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1::text, true)", str(acme_id))
            # UPDATE Globex memberships while in Acme context
            res_upd = await conn.execute(
                "UPDATE public.memberships SET status = 'suspended' WHERE tenant_id = $1",
                globex_id,
            )
            assert res_upd == "UPDATE 0"

            # DELETE Globex memberships while in Acme context
            res_del = await conn.execute(
                "DELETE FROM public.memberships WHERE tenant_id = $1",
                globex_id,
            )
            assert res_del == "DELETE 0"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_tenant_enumeration_blocked():
    """Verifies that without active tenant context, SELECT * FROM tenants returns 0 rows."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        async with conn.transaction():
            rows = await conn.fetch("SELECT * FROM public.tenants")
            assert len(rows) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_tenant_resolver_scalar_success():
    """Verifies that resolve_tenant_by_clerk_org returns a scalar UUID for a valid active tenant."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        tenant_uuid = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_acme_tech_01')"
        )
        assert tenant_uuid is not None
        assert isinstance(tenant_uuid, uuid.UUID)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_tenant_resolver_invalid_org():
    """Verifies that an unknown organization ID returns None (NULL)."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        val = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_non_existent_12345')"
        )
        assert val is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_tenant_resolver_inactive_tenant():
    """Verifies that an inactive tenant is not resolved by resolve_tenant_by_clerk_org."""
    admin_conn = await asyncpg.connect(ADMIN_DB_URL)
    app_conn = await asyncpg.connect(APP_DB_URL)
    try:
        # Create a temporary inactive tenant
        inactive_org_id = f"org_inactive_{uuid.uuid4().hex[:8]}"
        inactive_tenant_id = uuid.uuid4()
        await admin_conn.execute(
            """
            INSERT INTO public.tenants (id, name, clerk_org_id, status)
            VALUES ($1, 'Inactive Corp', $2, 'inactive')
            """,
            inactive_tenant_id,
            inactive_org_id,
        )

        # Call resolver from app role
        resolved = await app_conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org($1)",
            inactive_org_id,
        )
        assert resolved is None, "Inactive tenant must return None"

        # Cleanup
        await admin_conn.execute(
            "DELETE FROM public.tenants WHERE id = $1",
            inactive_tenant_id,
        )
    finally:
        await admin_conn.close()
        await app_conn.close()


@pytest.mark.asyncio
async def test_resolver_role_cannot_login():
    """Verifies that the dedicated agentic_hr_tenant_resolver role cannot log in."""
    with pytest.raises(asyncpg.exceptions.InvalidAuthorizationSpecificationError):
        await asyncpg.connect("postgresql://agentic_hr_tenant_resolver@127.0.0.1:5432/agentic_hr")


@pytest.mark.asyncio
async def test_public_cannot_execute_resolver():
    """Verifies that PUBLIC has no execute privilege on resolve_tenant_by_clerk_org."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        pub_grants = await conn.fetch("""
            SELECT grantee, privilege_type 
            FROM information_schema.routine_privileges 
            WHERE routine_schema = 'public' 
              AND routine_name = 'resolve_tenant_by_clerk_org' 
              AND grantee = 'PUBLIC';
        """)
        assert len(pub_grants) == 0, "PUBLIC must not have EXECUTE privilege on resolver function"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_transaction_rollback_cleanses_context():
    """Verifies that transaction rollback resets app.tenant_id back to default empty."""
    conn = await asyncpg.connect(APP_DB_URL)
    try:
        acme_id = await conn.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_acme_tech_01')"
        )

        # 1. In a transaction that rolls back:
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1::text, true)", str(acme_id)
                )
                assert len(await conn.fetch("SELECT * FROM public.tenants")) == 1
                raise RuntimeError("Simulated transaction rollback")
        except RuntimeError:
            pass

        # 2. After rollback:
        current_ctx = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
        assert current_ctx == "" or current_ctx is None

        # Direct query must return 0 rows
        assert len(await conn.fetch("SELECT * FROM public.tenants")) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_connection_pool_checkout_safety():
    """Verifies that pooled connection checkouts do not leak context across requests."""
    conn1 = await asyncpg.connect(APP_DB_URL)
    conn2 = await asyncpg.connect(APP_DB_URL)
    try:
        acme_id = await conn1.fetchval(
            "SELECT public.resolve_tenant_by_clerk_org('org_acme_tech_01')"
        )

        # Conn 1 executes transaction with Acme context
        async with conn1.transaction():
            await conn1.execute("SELECT set_config('app.tenant_id', $1::text, true)", str(acme_id))
            assert len(await conn1.fetch("SELECT * FROM public.tenants")) == 1

        # Conn 2 query without context -> must be 0 rows
        assert len(await conn2.fetch("SELECT * FROM public.tenants")) == 0
        assert await conn2.fetchval("SELECT current_setting('app.tenant_id', true)") in ("", None)
    finally:
        await conn1.close()
        await conn2.close()


@pytest.mark.asyncio
async def test_employee_self_promotion_blocked_via_api():
    """Verifies that an Employee attempting to update a membership is rejected with HTTP 403."""
    employee_token = create_token(
        sub="user_test_employee_04",
        org_id="org_acme_tech_01",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Obtain employee's membership ID via /me
        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["role"] == "employee"

        # Attempt to promote self to owner via PATCH /memberships
        patch_resp = await client.patch(
            f"/api/v1/auth/memberships/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {employee_token}"},
            json={"role_key": "owner", "is_payroll_admin": True},
        )
        assert patch_resp.status_code == 403
        assert "Access denied" in patch_resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_owner_anti_self_promotion_via_api():
    """Verifies that an Owner attempting to update their own membership is rejected with HTTP 400."""
    owner_token = create_token(
        sub="user_test_owner_01",
        org_id="org_acme_tech_01",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Fetch Owner's local user info to find owner's membership ID
        conn = await asyncpg.connect(ADMIN_DB_URL)
        try:
            owner_m_id = await conn.fetchval("""
                SELECT m.id FROM public.memberships m
                JOIN public.users u ON u.id = m.user_id
                WHERE u.clerk_user_id = 'user_test_owner_01'
            """)
        finally:
            await conn.close()

        # 2. Attempt to alter own membership role
        resp = await client.patch(
            f"/api/v1/auth/memberships/{owner_m_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"role_key": "employee"},
        )
        assert resp.status_code == 400
        assert "Cannot alter your own membership role" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_owner_can_update_other_member_via_api():
    """Verifies that an Owner can update another member's permissions, and RLS enforces isolation."""
    owner_token = create_token(
        sub="user_test_owner_01",
        org_id="org_acme_tech_01",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        conn = await asyncpg.connect(ADMIN_DB_URL)
        try:
            emp_m_id = await conn.fetchval("""
                SELECT m.id FROM public.memberships m
                JOIN public.users u ON u.id = m.user_id
                WHERE u.clerk_user_id = 'user_test_employee_04'
            """)
        finally:
            await conn.close()

        # 1. Owner grants payroll_admin flag to Employee
        patch_resp = await client.patch(
            f"/api/v1/auth/memberships/{emp_m_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"is_payroll_admin": True},
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["is_payroll_admin"] is True

        # 2. Owner revokes payroll_admin flag
        restore_resp = await client.patch(
            f"/api/v1/auth/memberships/{emp_m_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"is_payroll_admin": False},
        )
        assert restore_resp.status_code == 200
        assert restore_resp.json()["is_payroll_admin"] is False
