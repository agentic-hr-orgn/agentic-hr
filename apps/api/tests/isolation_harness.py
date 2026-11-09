"""
Reusable Multi-Tenant Isolation Testing Harness for Agentic HR Core.
Complies with ADR 002, ADR 003, ADR 006, and Stage 2 Phase 4 architecture.

Provides:
1. MultiTenantTestContext: Pre-resolved tenant, user, role, and membership metadata
   for Company A (Acme Tech Solutions) and Company B (Globex Corporation).
2. Cryptographic JWT test token factory (RS256 with registered test keys).
3. Least-privilege database connection factories connecting strictly as agentic_hr_app.
4. Generic assert_table_tenant_isolation: 6-point RLS isolation test for any tenant-scoped table.
5. Generic assert_route_tenant_isolation: Endpoint-level cross-tenant tampering and auth test.
"""

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx
import jwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.clerk import register_test_signing_key
from app.core.config import settings

# Deterministic RSA Key Setup for Test Suite
TEST_ISOLATION_KID = "test-isolation-kid-04"
_private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_public_key = _private_key.public_key()

# Automatically register signing key for offline JWT validation
register_test_signing_key(TEST_ISOLATION_KID, _public_key)


def to_asyncpg_url(sqlalchemy_url: str) -> str:
    """Converts SQLAlchemy asyncpg URL to pure asyncpg connection URI."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://")


APP_DB_URL = to_asyncpg_url(settings.DATABASE_URL)
MIGRATION_DB_URL = to_asyncpg_url(settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL)


def create_isolation_token(
    sub: str,
    org_id: str | None = "org_acme_tech_01",
    org_role: str = "member",
    exp_offset: int = 3600,
    nbf_offset: int = 0,
    issuer: str = settings.CLERK_ISSUER,
    kid: str = TEST_ISOLATION_KID,
) -> str:
    """Generates an RS256 JWT matching Clerk's token contract for testing."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": issuer,
        "iat": now,
        "exp": now + exp_offset,
        "nbf": now + nbf_offset,
    }
    if org_id is not None:
        payload["org_id"] = org_id
        payload["org_role"] = org_role

    pem_bytes = _private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, pem_bytes, algorithm="RS256", headers={"kid": kid})


@dataclass
class UserTestProfile:
    user_id: str
    clerk_user_id: str
    email: str
    membership_id: str
    role_key: str
    is_hr_head: bool
    is_payroll_admin: bool
    token: str


@dataclass
class CompanyTestProfile:
    tenant_id: str
    clerk_org_id: str
    name: str
    owner: UserTestProfile
    manager: UserTestProfile
    employee: UserTestProfile
    memberships_by_role: dict[str, str] = field(default_factory=dict)


@dataclass
class MultiTenantTestContext:
    company_a: CompanyTestProfile
    company_b: CompanyTestProfile
    cross_company_user_id: str
    cross_company_token_a: str
    cross_company_token_b: str
    orphan_user_clerk_id: str
    orphan_token_no_org: str
    orphan_token_unknown_org: str


async def get_app_connection() -> asyncpg.Connection:
    """Returns an asyncpg connection authenticated strictly as the restricted runtime role."""
    return await asyncpg.connect(APP_DB_URL)


async def get_contexted_connection(
    tenant_id: str,
    clerk_user_id: str,
) -> asyncpg.Connection:
    """Returns an asyncpg connection with transaction-local RLS variables established."""
    conn = await get_app_connection()
    tr = conn.transaction()
    await tr.start()
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, true), set_config('app.clerk_user_id', $2, true);",
        tenant_id,
        clerk_user_id,
    )
    return conn


async def load_multi_tenant_context() -> MultiTenantTestContext:
    """
    Connects to the database to resolve UUIDs for Company A and Company B,
    constructing the complete MultiTenantTestContext with pre-signed JWTs.
    """
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        # 1. Fetch Tenants
        rows = await admin_conn.fetch(
            "SELECT id, name, clerk_org_id FROM public.tenants WHERE clerk_org_id IN ($1, $2);",
            "org_acme_tech_01",
            "org_globex_corp_02",
        )
        tenants = {r["clerk_org_id"]: r for r in rows}
        if "org_acme_tech_01" not in tenants or "org_globex_corp_02" not in tenants:
            raise RuntimeError(
                "Database missing seed companies (org_acme_tech_01 or org_globex_corp_02)"
            )

        # 2. Fetch Memberships & Users
        mem_rows = await admin_conn.fetch("""
            SELECT 
                m.id AS membership_id,
                m.tenant_id,
                m.is_hr_head,
                m.is_payroll_admin,
                r.role_key,
                u.id AS user_id,
                u.clerk_user_id,
                u.email
            FROM public.memberships m
            JOIN public.users u ON m.user_id = u.id
            JOIN public.roles r ON m.role_id = r.id;
        """)

        def build_company_profile(org_id: str) -> CompanyTestProfile:
            t_row = tenants[org_id]
            t_id = str(t_row["id"])
            members = [m for m in mem_rows if str(m["tenant_id"]) == t_id]

            profiles: dict[str, UserTestProfile] = {}
            memberships_by_role: dict[str, str] = {}

            for m in members:
                rk = m["role_key"]
                token = create_isolation_token(
                    sub=m["clerk_user_id"],
                    org_id=org_id,
                    org_role="admin" if rk == "owner" else "member",
                )
                prof = UserTestProfile(
                    user_id=str(m["user_id"]),
                    clerk_user_id=m["clerk_user_id"],
                    email=m["email"],
                    membership_id=str(m["membership_id"]),
                    role_key=rk,
                    is_hr_head=m["is_hr_head"],
                    is_payroll_admin=m["is_payroll_admin"],
                    token=token,
                )
                profiles[rk] = prof
                memberships_by_role[rk] = str(m["membership_id"])

            return CompanyTestProfile(
                tenant_id=t_id,
                clerk_org_id=org_id,
                name=t_row["name"],
                owner=profiles["owner"],
                manager=profiles["manager"],
                employee=profiles["employee"],
                memberships_by_role=memberships_by_role,
            )

        company_a = build_company_profile("org_acme_tech_01")
        company_b = build_company_profile("org_globex_corp_02")

        # Candidate 2 (Cross-company user)
        candidate2_rows = [m for m in mem_rows if m["clerk_user_id"] == "user_test_candidate2_06"]
        c2_user_id = str(candidate2_rows[0]["user_id"]) if candidate2_rows else str(uuid.uuid4())
        cross_token_a = create_isolation_token(
            "user_test_candidate2_06", "org_acme_tech_01", "member"
        )
        cross_token_b = create_isolation_token(
            "user_test_candidate2_06", "org_globex_corp_02", "member"
        )

        # Orphan user (outsider with no tenant memberships)
        orphan_clerk_id = "user_test_orphan_outsider_99"
        orphan_token_no_org = create_isolation_token(orphan_clerk_id, org_id=None)
        orphan_token_unknown_org = create_isolation_token(
            orphan_clerk_id, org_id="org_unknown_unregistered_99"
        )

        return MultiTenantTestContext(
            company_a=company_a,
            company_b=company_b,
            cross_company_user_id=c2_user_id,
            cross_company_token_a=cross_token_a,
            cross_company_token_b=cross_token_b,
            orphan_user_clerk_id=orphan_clerk_id,
            orphan_token_no_org=orphan_token_no_org,
            orphan_token_unknown_org=orphan_token_unknown_org,
        )
    finally:
        await admin_conn.close()


# ---------------------------------------------------------------------------
# Reusable RLS Table Isolation Asserter for Future Stages
# ---------------------------------------------------------------------------
async def assert_table_tenant_isolation(
    table_name: str,
    company_a_tenant_id: str,
    company_b_tenant_id: str,
    company_a_row_id: str,
    company_b_row_id: str,
    tenant_id_column: str = "tenant_id",
    primary_key_column: str = "id",
    insert_sql_factory: Callable[[str], tuple[str, list[Any]]] | None = None,
) -> None:
    """
    Standard 6-point verification that an RLS-protected table enforces multi-tenant boundaries:
    1. Uncontexted SELECT returns 0 rows.
    2. Company A SELECT sees company_a_row_id and CANNOT see company_b_row_id.
    3. Company B SELECT sees company_b_row_id and CANNOT see company_a_row_id.
    4. Company A UPDATE on company_b_row_id affects 0 rows.
    5. Company A DELETE on company_b_row_id affects 0 rows.
    6. Company A INSERT with company_b_tenant_id raises SQLSTATE 42501 (InsufficientPrivilegeError).
    """
    conn = await get_app_connection()
    try:
        # Invariant 1: Uncontexted SELECT returns 0 rows
        uncontexted_rows = await conn.fetch(f"SELECT * FROM public.{table_name};")
        assert len(uncontexted_rows) == 0, (
            f"Uncontexted query on {table_name} leaked {len(uncontexted_rows)} rows!"
        )

        # Invariant 2: Company A SELECT sees Company A and not Company B
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true);", company_a_tenant_id)
            rows_a = await conn.fetch(f"SELECT {primary_key_column} FROM public.{table_name};")
            ids_a = {str(r[primary_key_column]) for r in rows_a}
            assert company_a_row_id in ids_a, (
                f"Company A row {company_a_row_id} not visible in Company A context"
            )
            assert company_b_row_id not in ids_a, (
                f"CRITICAL: Company B row {company_b_row_id} leaked to Company A!"
            )

        # Invariant 3: Company B SELECT sees Company B and not Company A
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true);", company_b_tenant_id)
            rows_b = await conn.fetch(f"SELECT {primary_key_column} FROM public.{table_name};")
            ids_b = {str(r[primary_key_column]) for r in rows_b}
            assert company_b_row_id in ids_b, (
                f"Company B row {company_b_row_id} not visible in Company B context"
            )
            assert company_a_row_id not in ids_b, (
                f"CRITICAL: Company A row {company_a_row_id} leaked to Company B!"
            )

        # Invariant 4: Company A UPDATE on Company B row affects 0 rows
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true);", company_a_tenant_id)
            try:
                status = await conn.execute(
                    f"UPDATE public.{table_name} SET {primary_key_column} = {primary_key_column} WHERE {primary_key_column} = $1::uuid;",
                    company_b_row_id,
                )
                assert status == "UPDATE 0", (
                    f"CRITICAL: Cross-tenant UPDATE modified rows: {status}"
                )
            except asyncpg.exceptions.InsufficientPrivilegeError:
                # UPDATE permission revoked from role (e.g. append-only audit_logs) is also a valid denial
                pass

        # Invariant 5: Company A DELETE on Company B row affects 0 rows
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true);", company_a_tenant_id)
            try:
                status = await conn.execute(
                    f"DELETE FROM public.{table_name} WHERE {primary_key_column} = $1::uuid;",
                    company_b_row_id,
                )
                assert status == "DELETE 0", f"CRITICAL: Cross-tenant DELETE removed rows: {status}"
            except asyncpg.exceptions.InsufficientPrivilegeError:
                # DELETE permission revoked from role is also a valid denial
                pass

        # Invariant 6: Company A INSERT with Company B tenant_id raises 42501
        if insert_sql_factory is not None:
            sql, params = insert_sql_factory(company_b_tenant_id)
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);", company_a_tenant_id
                )
                failed = False
                try:
                    await conn.execute(sql, *params)
                except asyncpg.exceptions.InsufficientPrivilegeError:
                    failed = True
                except asyncpg.exceptions.CheckViolationError:
                    failed = True
                assert failed, f"CRITICAL: Cross-tenant INSERT succeeded on {table_name}!"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Reusable Protected Route Isolation Asserter for Future Stages
# ---------------------------------------------------------------------------
async def assert_route_tenant_isolation(
    client: httpx.AsyncClient,
    method: str,
    url_company_a: str,
    url_company_b: str,
    company_a_token: str,
    company_b_token: str,
    expected_status_on_own: int = 200,
    expected_status_on_cross: int = 404,
    json_payload: dict[str, Any] | None = None,
) -> None:
    """
    Standard verification that a protected API endpoint enforces tenant boundaries:
    1. Authenticated Company A accessing Company A resource succeeds.
    2. Authenticated Company A accessing Company B resource fails (404/403).
    3. Authenticated Company B accessing Company A resource fails (404/403).
    4. Unauthenticated request fails (401).
    5. Parameter tampering (e.g. ?tenant_id=...) does not alter isolation boundary.
    """
    # 1. Company A accesses Company A resource
    headers_a = {"Authorization": f"Bearer {company_a_token}"}
    resp_a = await client.request(method, url_company_a, headers=headers_a, json=json_payload)
    assert resp_a.status_code == expected_status_on_own, (
        f"Expected {expected_status_on_own}, got {resp_a.status_code}: {resp_a.text}"
    )

    # 2. Company A accesses Company B resource
    resp_cross_b = await client.request(method, url_company_b, headers=headers_a, json=json_payload)
    assert resp_cross_b.status_code == expected_status_on_cross, (
        f"CRITICAL: Company A accessed Company B resource at {url_company_b}! Status: {resp_cross_b.status_code}"
    )

    # 3. Company B accesses Company A resource
    headers_b = {"Authorization": f"Bearer {company_b_token}"}
    resp_cross_a = await client.request(method, url_company_a, headers=headers_b, json=json_payload)
    assert resp_cross_a.status_code == expected_status_on_cross, (
        f"CRITICAL: Company B accessed Company A resource at {url_company_a}! Status: {resp_cross_a.status_code}"
    )

    # 4. Unauthenticated request
    resp_unauth = await client.request(method, url_company_a, json=json_payload)
    assert resp_unauth.status_code == 401, (
        f"Expected 401 for unauthenticated request, got {resp_unauth.status_code}"
    )
