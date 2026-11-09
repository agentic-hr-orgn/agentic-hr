"""
Automated Pytest Suite for Clerk Authentication & Multi-Tenant RBAC.
Tests all 8 mandatory security and tenant isolation scenarios:
1. Valid login with active organization (returns profile, role, permissions).
2. Invalid token (malformed structure or invalid signature).
3. Expired token (RS256 JWT with past exp rejected with 401).
4. Missing organization (token without org_id rejected with 403).
5. Unknown organization (org_id not matching any tenant rejected with 403).
6. User not a member (registered user requesting unauthorized tenant rejected with 403).
7. User with multiple memberships (candidate2 accesses Acme Tech as employee, Globex as manager).
8. Role-based access control (owner, manager, hr_head, payroll_admin gating).
"""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.clerk import clear_test_signing_keys, register_test_signing_key
from app.core.config import settings

# Generate deterministic RSA key pair for cryptographic JWT testing
TEST_KID = "test-rsa-key-01"
_private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_public_key = _private_key.public_key()
_public_pem = _public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("utf-8")


@pytest.fixture(autouse=True)
def setup_test_keys():
    """Registers offline public RSA test key for signature verification."""
    register_test_signing_key(TEST_KID, _public_key)
    yield
    clear_test_signing_keys()


def create_signed_token(
    sub: str,
    org_id: str | None = "org_acme_tech_01",
    exp_offset: int = 3600,
    issuer: str = settings.CLERK_ISSUER,
    kid: str = TEST_KID,
) -> str:
    """Helper creating cryptographically signed RS256 JWT tokens for testing."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "iss": issuer,
        "iat": now,
        "nbf": now - 10,
        "exp": now + exp_offset,
    }
    if org_id is not None:
        payload["org_id"] = org_id

    headers = {"kid": kid, "alg": "RS256"}
    return jwt.encode(payload, _private_key, algorithm="RS256", headers=headers)


@pytest.mark.asyncio
async def test_01_valid_login_with_active_organization(async_client: httpx.AsyncClient):
    """Scenario 1: Authenticated user with valid token and active org retrieves profile and role."""
    token = create_signed_token(
        sub="agentic.hr.test.ubaid.owner@gmail.com",
        org_id="org_acme_tech_01",
    )
    headers = {"Authorization": f"Bearer {token}"}

    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["email"] == "agentic.hr.test.ubaid.owner@gmail.com"
    assert data["clerk_org_id"] == "org_acme_tech_01"
    assert data["tenant_name"] == "Acme Tech Solutions"
    assert data["role"] == "owner"
    assert data["is_hr_head"] is True
    assert data["is_payroll_admin"] is True
    assert "admin" in data["permissions"]
    assert "owner" in data["permissions"]


@pytest.mark.asyncio
async def test_02_invalid_token_signature_and_format(async_client: httpx.AsyncClient):
    """Scenario 2: Malformed tokens or forged signatures return 401 Unauthorized."""
    # Test 2a: Completely malformed token
    response_malformed = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt-token"},
    )
    assert response_malformed.status_code == 401
    err = response_malformed.json()["error"]
    assert err["code"] == "UNAUTHORIZED"

    # Test 2b: Token signed with a different, unauthorized RSA key
    other_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    now = int(time.time())
    forged_token = jwt.encode(
        {"sub": "attacker", "iss": settings.CLERK_ISSUER, "exp": now + 3600},
        other_key,
        algorithm="RS256",
        headers={"kid": "unknown-kid"},
    )
    response_forged = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {forged_token}"},
    )
    assert response_forged.status_code == 401


@pytest.mark.asyncio
async def test_03_expired_token(async_client: httpx.AsyncClient):
    """Scenario 3: Valid signature but expired timestamp returns 401 Unauthorized."""
    # exp_offset = -60 puts expiration 1 minute in the past
    expired_token = create_signed_token(
        sub="agentic.hr.test.ubaid.owner@gmail.com",
        org_id="org_acme_tech_01",
        exp_offset=-60,
    )
    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_04_missing_organization(async_client: httpx.AsyncClient):
    """Scenario 4: Valid user token without an active org claim (org_id=None) returns 403 Forbidden."""
    token_without_org = create_signed_token(
        sub="agentic.hr.test.ubaid.owner@gmail.com",
        org_id=None,
    )
    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_without_org}"},
    )
    assert response.status_code == 403
    assert "No active organization" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_05_unknown_organization(async_client: httpx.AsyncClient):
    """Scenario 5: Token referencing an unknown organization returns 403 Forbidden."""
    token_unknown_org = create_signed_token(
        sub="agentic.hr.test.ubaid.owner@gmail.com",
        org_id="org_unregistered_tenant_999",
    )
    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_unknown_org}"},
    )
    assert response.status_code == 403
    assert "not registered" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_06_user_not_a_member(async_client: httpx.AsyncClient):
    """Scenario 6: User exists but has no membership in the requested organization."""
    # Candidate 1 belongs strictly to Globex Corp (org_globex_corp_02)
    token_unauthorized_org = create_signed_token(
        sub="agentic.hr.test.ubaid.candidate1@gmail.com",
        org_id="org_acme_tech_01",
    )
    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_unauthorized_org}"},
    )
    assert response.status_code == 403
    assert "not a member" in response.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_07_user_with_multiple_memberships(async_client: httpx.AsyncClient):
    """Scenario 7: Candidate 2 belongs to both Acme Tech and Globex with distinct roles."""
    # 7a: Access Acme Tech -> Employee role, HR Head is False
    acme_token = create_signed_token(
        sub="agentic.hr.test.ubaid.candidate2@gmail.com",
        org_id="org_acme_tech_01",
    )
    res_acme = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {acme_token}"},
    )
    assert res_acme.status_code == 200
    data_acme = res_acme.json()
    assert data_acme["tenant_name"] == "Acme Tech Solutions"
    assert data_acme["role"] == "employee"
    assert data_acme["is_hr_head"] is False

    # 7b: Access Globex Corp -> Manager role, HR Head is True
    globex_token = create_signed_token(
        sub="agentic.hr.test.ubaid.candidate2@gmail.com",
        org_id="org_globex_corp_02",
    )
    res_globex = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {globex_token}"},
    )
    assert res_globex.status_code == 200
    data_globex = res_globex.json()
    assert data_globex["tenant_name"] == "Globex Corporation"
    assert data_globex["role"] == "manager"
    assert data_globex["is_hr_head"] is True


@pytest.mark.asyncio
async def test_08_role_based_access_control(async_client: httpx.AsyncClient):
    """Scenario 8: Role and permission gating endpoints (owner, manager, hr_head, payroll_admin)."""
    owner_token = create_signed_token(
        sub="agentic.hr.test.ubaid.owner@gmail.com",
        org_id="org_acme_tech_01",
    )
    recruiter_token = create_signed_token(
        sub="agentic.hr.test.ubaid.recruiter@gmail.com",
        org_id="org_acme_tech_01",
    )
    employee_token = create_signed_token(
        sub="agentic.hr.test.ubaid.employee@gmail.com",
        org_id="org_acme_tech_01",
    )

    owner_hdr = {"Authorization": f"Bearer {owner_token}"}
    recruiter_hdr = {"Authorization": f"Bearer {recruiter_token}"}
    employee_hdr = {"Authorization": f"Bearer {employee_token}"}

    # 8a: /test/owner endpoint
    assert (await async_client.get("/api/v1/auth/test/owner", headers=owner_hdr)).status_code == 200
    assert (
        await async_client.get("/api/v1/auth/test/owner", headers=recruiter_hdr)
    ).status_code == 403
    assert (
        await async_client.get("/api/v1/auth/test/owner", headers=employee_hdr)
    ).status_code == 403

    # 8b: /test/manager endpoint (allows owner and manager)
    assert (
        await async_client.get("/api/v1/auth/test/manager", headers=owner_hdr)
    ).status_code == 200
    assert (
        await async_client.get("/api/v1/auth/test/manager", headers=recruiter_hdr)
    ).status_code == 200
    assert (
        await async_client.get("/api/v1/auth/test/manager", headers=employee_hdr)
    ).status_code == 403

    # 8c: /test/hr-head endpoint (owner and recruiter have HR Head flag)
    assert (
        await async_client.get("/api/v1/auth/test/hr-head", headers=owner_hdr)
    ).status_code == 200
    assert (
        await async_client.get("/api/v1/auth/test/hr-head", headers=recruiter_hdr)
    ).status_code == 200
    assert (
        await async_client.get("/api/v1/auth/test/hr-head", headers=employee_hdr)
    ).status_code == 403

    # 8d: /test/payroll-admin endpoint (only owner has payroll_admin flag)
    assert (
        await async_client.get("/api/v1/auth/test/payroll-admin", headers=owner_hdr)
    ).status_code == 200
    assert (
        await async_client.get("/api/v1/auth/test/payroll-admin", headers=recruiter_hdr)
    ).status_code == 403
    assert (
        await async_client.get("/api/v1/auth/test/payroll-admin", headers=employee_hdr)
    ).status_code == 403


@pytest.mark.asyncio
async def test_09_mock_token_authentication(async_client: httpx.AsyncClient):
    """Validates that mock tokens work seamlessly in test/dev mode."""
    headers = {
        "Authorization": "Bearer mock:agentic.hr.test.ubaid.owner@gmail.com:org_acme_tech_01"
    }
    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "owner"
    assert data["tenant_name"] == "Acme Tech Solutions"
