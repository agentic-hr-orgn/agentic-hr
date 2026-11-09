"""
Comprehensive Automated Test Suite for Redis Rate Limiting & Tenant Kill Switch.
Complies with ADR 002 (Tenancy & Infrastructure), ADR 003, and Stage 2 Phase 10 specifications.

Tests:
1. Normal tenant request (200 OK + rate-limit headers).
2. Suspended tenant request (403 Forbidden).
3. Unauthorized switch change (403 Forbidden for non-owners).
4. Cross-tenant switch attempt (403 Forbidden).
5. Redis rate limit reached (429 Too Many Requests + Retry-After).
6. Redis unavailable (graceful degradation when fail-open vs 503 when fail-closed).
7. Tenant A suspended while Tenant B remains fully operational (and independent rate limit quota).
8. Kill switch change audited (audit_logs populated for suspend and restore).
9. Public health and documentation routes exempt from rate limits and tenant kill-switch.
10. Database status check failure fails closed (never silently proceeds).
"""

import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import httpx
import pytest
import pytest_asyncio
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import settings
from app.core.rate_limit import check_rate_limit
from app.core.redis import get_redis_client
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
)


@pytest.fixture(autouse=True)
def setup_isolation_keys():
    """Registers offline test RSA key for signature verification."""
    from app.core.clerk import register_test_signing_key

    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    yield


@pytest_asyncio.fixture(autouse=True)
async def cleanup_tenant_and_redis(isolation_context: MultiTenantTestContext):
    """Resets tenant kill-switch states and flushes Redis rate-limit keys after each test."""
    yield
    # 1. Restore tenant states in PostgreSQL
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await admin_conn.execute(
            """
            UPDATE public.tenants
            SET is_suspended = false,
                status = 'active',
                suspended_at = NULL,
                suspended_by = NULL,
                suspension_reason = NULL,
                updated_at = NOW()
            WHERE id = ANY($1::uuid[])
            """,
            [
                uuid.UUID(isolation_context.company_a.tenant_id),
                uuid.UUID(isolation_context.company_b.tenant_id),
            ],
        )
    finally:
        await admin_conn.close()

    # 2. Flush Redis rate limit keys
    try:
        client = await get_redis_client()
        keys = await client.keys("rl:*")
        if keys:
            await client.delete(*keys)
    except Exception as exc:
        import logging

        logging.getLogger("test").debug("Redis cleanup skipped: %s", exc)


# -------------------------------------------------------------------------
# Test 1: Normal Tenant Request
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_01_normal_tenant_request(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that an active tenant user successfully accesses protected routes with rate-limit headers."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}

    res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"

    data = res.json()
    assert data["email"] == ctx.company_a.employee.email
    assert data["tenant_id"] == ctx.company_a.tenant_id
    assert data["is_tenant_suspended"] is False

    # Also test an endpoint guarded by RateLimiter
    res_kill = await async_client.get(
        f"/api/v1/tenants/{ctx.company_a.tenant_id}/kill-switch",
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
    )
    assert res_kill.status_code == 200
    assert "X-RateLimit-Limit" in res_kill.headers
    assert "X-RateLimit-Remaining" in res_kill.headers


# -------------------------------------------------------------------------
# Test 2: Suspended Tenant Request
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_02_suspended_tenant_request(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that once a tenant is suspended, all normal employee/manager operations return 403."""
    ctx = isolation_context

    # 1. Owner suspends Tenant A
    suspend_res = await async_client.post(
        f"/api/v1/tenants/{ctx.company_a.tenant_id}/kill-switch",
        json={"is_suspended": True, "reason": "Overdue platform subscription"},
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
    )
    assert suspend_res.status_code == 200, suspend_res.text
    assert suspend_res.json()["is_suspended"] is True
    assert suspend_res.json()["status"] == "suspended"

    # 2. Employee of Tenant A tries to access /api/v1/auth/me
    emp_res = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {ctx.company_a.employee.token}"},
    )
    assert emp_res.status_code == 403
    assert "Tenant organization is suspended" in emp_res.text

    # 3. Manager of Tenant A tries to access /api/v1/approvals
    mgr_res = await async_client.get(
        "/api/v1/approvals",
        headers={"Authorization": f"Bearer {ctx.company_a.manager.token}"},
    )
    assert mgr_res.status_code == 403
    assert "Tenant organization is suspended" in mgr_res.text


# -------------------------------------------------------------------------
# Test 3: Unauthorized Switch Change
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_03_unauthorized_switch_change(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that non-owner users cannot toggle the kill-switch."""
    ctx = isolation_context

    # 1. Employee attempts to suspend tenant
    emp_res = await async_client.post(
        f"/api/v1/tenants/{ctx.company_a.tenant_id}/kill-switch",
        json={"is_suspended": True, "reason": "Unauthorized malicious attempt"},
        headers={"Authorization": f"Bearer {ctx.company_a.employee.token}"},
    )
    assert emp_res.status_code == 403
    assert "Unauthorized switch change" in emp_res.text

    # 2. Manager attempts to suspend tenant
    mgr_res = await async_client.post(
        f"/api/v1/tenants/{ctx.company_a.tenant_id}/kill-switch",
        json={"is_suspended": True, "reason": "Unauthorized manager attempt"},
        headers={"Authorization": f"Bearer {ctx.company_a.manager.token}"},
    )
    assert mgr_res.status_code == 403
    assert "Unauthorized switch change" in mgr_res.text


# -------------------------------------------------------------------------
# Test 4: Cross-Tenant Switch Attempt
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_04_cross_tenant_switch_attempt(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that Owner of Tenant A cannot tamper with Tenant B's kill-switch (browser untrusted)."""
    ctx = isolation_context

    # Owner of Company A attempts to suspend Company B
    cross_res = await async_client.post(
        f"/api/v1/tenants/{ctx.company_b.tenant_id}/kill-switch",
        json={"is_suspended": True, "reason": "Sabotaging competitor company"},
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
    )
    assert cross_res.status_code == 403
    assert "Cross-tenant switch attempt" in cross_res.text

    # Verify Company B remained unaffected
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        b_status = await admin_conn.fetchrow(
            "SELECT is_suspended, status FROM public.tenants WHERE id = $1",
            uuid.UUID(ctx.company_b.tenant_id),
        )
        assert b_status["is_suspended"] is False
        assert b_status["status"] == "active"
    finally:
        await admin_conn.close()


# -------------------------------------------------------------------------
# Test 5: Redis Rate Limit Reached
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_05_redis_rate_limit_reached() -> None:
    """Verifies that exceeding the rate limit quota returns 429 with Retry-After information."""
    tenant_id = f"test_tenant_{uuid.uuid4().hex[:6]}"
    user_id = f"test_user_{uuid.uuid4().hex[:6]}"
    scope = "quota_exhaustion_test"
    max_requests = 3
    window_seconds = 10

    # First 3 requests must succeed
    r1 = await check_rate_limit(tenant_id, user_id, scope, max_requests, window_seconds)
    assert r1.allowed is True
    assert r1.remaining == 2

    r2 = await check_rate_limit(tenant_id, user_id, scope, max_requests, window_seconds)
    assert r2.allowed is True
    assert r2.remaining == 1

    r3 = await check_rate_limit(tenant_id, user_id, scope, max_requests, window_seconds)
    assert r3.allowed is True
    assert r3.remaining == 0

    # 4th request must be rejected
    r4 = await check_rate_limit(tenant_id, user_id, scope, max_requests, window_seconds)
    assert r4.allowed is False
    assert r4.remaining == 0
    assert r4.retry_after > 0
    assert r4.retry_after <= window_seconds


# -------------------------------------------------------------------------
# Test 6: Redis Unavailable
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_06_redis_unavailable_graceful_handling() -> None:
    """Verifies system behavior when Redis connectivity fails (fail-open vs fail-closed)."""
    mock_redis = AsyncMock()
    mock_redis.eval.side_effect = RedisConnectionError("Redis connection refused")

    # 1. When fail-open is enabled (default)
    with patch.object(settings, "RATE_LIMIT_FAIL_OPEN", True):
        res = await check_rate_limit(
            tenant_id="tenant_degraded",
            user_id="user_degraded",
            scope="test",
            max_requests=10,
            window_seconds=60,
            redis_client=mock_redis,
        )
        assert res.allowed is True
        assert res.degraded is True

    # 2. When fail-open is disabled (strict 503)
    with patch.object(settings, "RATE_LIMIT_FAIL_OPEN", False):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await check_rate_limit(
                tenant_id="tenant_strict",
                user_id="user_strict",
                scope="test",
                max_requests=10,
                window_seconds=60,
                redis_client=mock_redis,
            )
        assert exc_info.value.status_code == 503
        assert "Rate limiter service temporarily unavailable" in exc_info.value.detail


# -------------------------------------------------------------------------
# Test 7: Tenant A Suspended While Tenant B Remains Operational
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_07_tenant_a_suspended_while_tenant_b_operational(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that suspending Tenant A has zero impact on Tenant B's operations or rate-limit quota."""
    ctx = isolation_context

    # 1. Suspend Tenant A
    suspend_res = await async_client.post(
        f"/api/v1/tenants/{ctx.company_a.tenant_id}/kill-switch",
        json={"is_suspended": True, "reason": "Tenant A isolated for maintenance"},
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
    )
    assert suspend_res.status_code == 200

    # 2. Tenant A requests fail with 403
    res_a = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {ctx.company_a.employee.token}"},
    )
    assert res_a.status_code == 403

    # 3. Tenant B requests succeed with 200
    res_b = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {ctx.company_b.employee.token}"},
    )
    assert res_b.status_code == 200
    assert res_b.json()["tenant_id"] == ctx.company_b.tenant_id

    # 4. Rate-limit quota isolation: Exceed Tenant A's quota on a scope
    scope = "isolated_quota_test"
    for _ in range(5):
        await check_rate_limit(
            tenant_id=ctx.company_a.tenant_id,
            user_id="shared_scope_test",
            scope=scope,
            max_requests=5,
            window_seconds=60,
        )

    # 6th request for Tenant A is rejected
    res_a_quota = await check_rate_limit(
        tenant_id=ctx.company_a.tenant_id,
        user_id="shared_scope_test",
        scope=scope,
        max_requests=5,
        window_seconds=60,
    )
    assert res_a_quota.allowed is False

    # Tenant B quota for the same scope remains completely full (5 remaining)
    res_b_quota = await check_rate_limit(
        tenant_id=ctx.company_b.tenant_id,
        user_id="shared_scope_test",
        scope=scope,
        max_requests=5,
        window_seconds=60,
    )
    assert res_b_quota.allowed is True
    assert res_b_quota.remaining == 4


# -------------------------------------------------------------------------
# Test 8: Kill Switch Change Audited
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_08_kill_switch_change_audited(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that suspending and restoring a tenant writes immutable records to audit_logs."""
    ctx = isolation_context

    # 1. Suspend Tenant
    suspend_reason = "Regulatory freeze requested by legal"
    await async_client.post(
        f"/api/v1/tenants/{ctx.company_a.tenant_id}/kill-switch",
        json={"is_suspended": True, "reason": suspend_reason},
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
    )

    # 2. Restore Tenant
    restore_reason = "Regulatory review completed successfully"
    await async_client.post(
        f"/api/v1/tenants/{ctx.company_a.tenant_id}/kill-switch",
        json={"is_suspended": False, "reason": restore_reason},
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
    )

    # 3. Query audit_logs
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        records = await admin_conn.fetch(
            """
            SELECT action, target_type, target_id, actor_id, tenant_id, details
            FROM public.audit_logs
            WHERE tenant_id = $1 AND target_type = 'tenant'
            ORDER BY timestamp DESC
            LIMIT 2
            """,
            uuid.UUID(ctx.company_a.tenant_id),
        )
        assert len(records) >= 2

        # Most recent record is restore
        rec_restore = records[0]
        assert rec_restore["action"] == "tenant.restore"
        assert str(rec_restore["actor_id"]) == ctx.company_a.owner.user_id
        import json

        details_restore = (
            json.loads(rec_restore["details"])
            if isinstance(rec_restore["details"], str)
            else rec_restore["details"]
        )
        assert details_restore["reason"] == restore_reason
        assert details_restore["is_suspended"] is False

        # Preceding record is suspend
        rec_suspend = records[1]
        assert rec_suspend["action"] == "tenant.suspend"
        assert str(rec_suspend["actor_id"]) == ctx.company_a.owner.user_id
        details_suspend = (
            json.loads(rec_suspend["details"])
            if isinstance(rec_suspend["details"], str)
            else rec_suspend["details"]
        )
        assert details_suspend["reason"] == suspend_reason
        assert details_suspend["is_suspended"] is True
    finally:
        await admin_conn.close()


# -------------------------------------------------------------------------
# Test 9: Public and Health Routes Exempt
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_09_public_routes_exempt(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that public health probes and OpenAPI docs are never blocked by tenant suspension or rate limits."""
    ctx = isolation_context

    # Suspend Company A
    await async_client.post(
        f"/api/v1/tenants/{ctx.company_a.tenant_id}/kill-switch",
        json={"is_suspended": True, "reason": "Testing public exemptions"},
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
    )

    # Health probes must return 200
    res_health1 = await async_client.get("/health")
    assert res_health1.status_code == 200

    res_health2 = await async_client.get("/api/health")
    assert res_health2.status_code == 200

    # OpenAPI docs must return 200
    res_docs = await async_client.get("/docs")
    assert res_docs.status_code == 200


# -------------------------------------------------------------------------
# Test 10: Database Status Unreliable Fails Closed
# -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_10_database_status_unreliable_fails_closed(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that if the database cannot be queried reliably, protected requests fail closed immediately."""
    from app.main import app

    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.core.security.select") as mock_select:
            mock_select.side_effect = RuntimeError("Database connection interrupted")
            res = await client.get("/api/v1/auth/me", headers=headers)
            # Must fail-closed: 500 error, never 200
            assert res.status_code == 500
            assert res.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
