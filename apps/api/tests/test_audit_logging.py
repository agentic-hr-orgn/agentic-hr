"""
Comprehensive Automated Test Suite for Audit Logging System.
Complies with ADR 002, ADR 003, and Stage 2 Phase 5 specifications.

Verifies:
1. Audit entry created with full metadata.
2. Correct actor and tenant context recorded.
3. UPDATE rejected at both privilege (42501) and trigger (55000) levels.
4. DELETE rejected at both privilege (42501) and trigger (55000) levels.
5. Cross-tenant access rejected via Row-Level Security (RLS).
6. Failed business operations roll back audit logs (zero misleading success logs).
7. Successful business mutations commit atomically and consistently with audit logs.
8. Pakistani CNIC and credential redaction (passwords, API keys, tokens).
9. Output sample synthetic audit records.
"""

import json
import uuid

import asyncpg
import httpx
import pytest

from app.core.clerk import register_test_signing_key
from app.db.session import AsyncSessionLocal
from app.models.tenant import set_tenant_context
from app.services.audit_service import sanitize_value, write_audit
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
    assert_table_tenant_isolation,
    get_app_connection,
)


@pytest.fixture(autouse=True)
def setup_isolation_keys():
    """Ensure isolation test signing keys are registered for API calls."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    yield


@pytest.mark.asyncio
async def test_audit_entry_created(
    isolation_context: MultiTenantTestContext,
) -> None:
    """1. Verifies that write_audit() writes an entry to audit_logs with all required fields."""
    ctx = isolation_context

    async with AsyncSessionLocal() as session:
        await set_tenant_context(
            session, ctx.company_a.tenant_id, ctx.company_a.owner.clerk_user_id
        )

        target_uuid = uuid.uuid4()
        entry = await write_audit(
            session,
            action="candidate.screen",
            target_type="candidate",
            target_id=target_uuid,
            details={"rubric_score": 88, "notes": "Strong candidate"},
            actor_id=ctx.company_a.owner.user_id,
            tenant_id=ctx.company_a.tenant_id,
            request_id="test-req-001",
        )
        await session.commit()
        audit_id = entry.id

    # Verify directly via restricted application connection
    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            row = await conn.fetchrow(
                "SELECT * FROM public.audit_logs WHERE id = $1::uuid;", audit_id
            )
            assert row is not None
            assert str(row["tenant_id"]) == ctx.company_a.tenant_id
            assert str(row["actor_id"]) == ctx.company_a.owner.user_id
            assert row["action"] == "candidate.screen"
            assert row["target_type"] == "candidate"
            assert row["target_id"] == str(target_uuid)
            assert row["request_id"] == "test-req-001"
            details = json.loads(row["details"])
            assert details["rubric_score"] == 88
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_audit_actor_and_tenant_correct(
    isolation_context: MultiTenantTestContext,
) -> None:
    """2. Verifies that actor_id and tenant_id are recorded accurately."""
    ctx = isolation_context

    async with AsyncSessionLocal() as session:
        await set_tenant_context(
            session, ctx.company_b.tenant_id, ctx.company_b.owner.clerk_user_id
        )
        entry = await write_audit(
            session,
            action="policy.query",
            target_type="policy",
            target_id=None,
            details={"query": "annual leave policy"},
            actor_id=ctx.company_b.owner.user_id,
            tenant_id=ctx.company_b.tenant_id,
        )
        await session.commit()
        audit_id = entry.id

    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_b.tenant_id
            )
            row = await conn.fetchrow(
                "SELECT tenant_id, actor_id FROM public.audit_logs WHERE id = $1::uuid;",
                audit_id,
            )
            assert row is not None
            assert str(row["tenant_id"]) == ctx.company_b.tenant_id
            assert str(row["actor_id"]) == ctx.company_b.owner.user_id
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_audit_update_rejected_by_privileges_and_trigger(
    isolation_context: MultiTenantTestContext,
) -> None:
    """3. Verifies that UPDATE on audit_logs is rejected by both role privileges (42501) and trigger (55000)."""
    ctx = isolation_context

    # Seed an audit entry
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id)
        entry = await write_audit(
            session,
            action="test.immutable",
            target_type="test",
            target_id="123",
            tenant_id=ctx.company_a.tenant_id,
        )
        await session.commit()
        audit_id = entry.id

    # 3a. Restricted Application Role (agentic_hr_app): Denied at Privilege Level (42501)
    app_conn = await get_app_connection()
    try:
        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            privilege_blocked = False
            try:
                await app_conn.execute(
                    "UPDATE public.audit_logs SET action = 'tampered' WHERE id = $1::uuid;",
                    audit_id,
                )
            except asyncpg.exceptions.InsufficientPrivilegeError:
                privilege_blocked = True
            assert privilege_blocked, "CRITICAL: agentic_hr_app was permitted to UPDATE audit_logs!"
    finally:
        await app_conn.close()

    # 3b. Admin Role (agentic_hr_migrator / postgres): Denied by Trigger Level (55000)
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        trigger_blocked = False
        try:
            await admin_conn.execute(
                "UPDATE public.audit_logs SET action = 'tampered' WHERE id = $1::uuid;",
                audit_id,
            )
        except asyncpg.exceptions.PostgresError as exc:
            # Trigger raises exception with SQLSTATE 55000
            if "immutable and append-only" in str(exc) or exc.sqlstate == "55000":
                trigger_blocked = True
        assert trigger_blocked, "CRITICAL: Database trigger failed to block UPDATE on audit_logs!"
    finally:
        await admin_conn.close()


@pytest.mark.asyncio
async def test_audit_delete_rejected_by_privileges_and_trigger(
    isolation_context: MultiTenantTestContext,
) -> None:
    """4. Verifies that DELETE on audit_logs is rejected by both role privileges (42501) and trigger (55000)."""
    ctx = isolation_context

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id)
        entry = await write_audit(
            session,
            action="test.nodelete",
            target_type="test",
            target_id="456",
            tenant_id=ctx.company_a.tenant_id,
        )
        await session.commit()
        audit_id = entry.id

    # 4a. Restricted Application Role (agentic_hr_app): Denied at Privilege Level (42501)
    app_conn = await get_app_connection()
    try:
        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            privilege_blocked = False
            try:
                await app_conn.execute(
                    "DELETE FROM public.audit_logs WHERE id = $1::uuid;", audit_id
                )
            except asyncpg.exceptions.InsufficientPrivilegeError:
                privilege_blocked = True
            assert privilege_blocked, "CRITICAL: agentic_hr_app was permitted to DELETE audit_logs!"
    finally:
        await app_conn.close()

    # 4b. Admin Role: Denied by Trigger Level (55000)
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        trigger_blocked = False
        try:
            await admin_conn.execute("DELETE FROM public.audit_logs WHERE id = $1::uuid;", audit_id)
        except asyncpg.exceptions.PostgresError as exc:
            if "immutable and append-only" in str(exc) or exc.sqlstate == "55000":
                trigger_blocked = True
        assert trigger_blocked, "CRITICAL: Database trigger failed to block DELETE on audit_logs!"
    finally:
        await admin_conn.close()


@pytest.mark.asyncio
async def test_audit_tenant_isolation_rls(
    isolation_context: MultiTenantTestContext,
) -> None:
    """5. Verifies that audit_logs enforces multi-tenant Row-Level Security (RLS)."""
    ctx = isolation_context

    # Create one audit entry in Company A and one in Company B
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id)
        entry_a = await write_audit(
            session,
            action="company_a.event",
            target_type="test",
            target_id="A1",
            tenant_id=ctx.company_a.tenant_id,
        )
        await session.commit()
        id_a = str(entry_a.id)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_b.tenant_id)
        entry_b = await write_audit(
            session,
            action="company_b.event",
            target_type="test",
            target_id="B1",
            tenant_id=ctx.company_b.tenant_id,
        )
        await session.commit()
        id_b = str(entry_b.id)

    # Use the reusable harness to verify all 6 isolation invariants
    def audit_insert_factory(target_tenant_id: str) -> tuple[str, list]:
        sql = """
            INSERT INTO public.audit_logs (id, tenant_id, action, target_type, target_id, details)
            VALUES ($1, $2::uuid, 'rogue.insert', 'test', 'R1', '{}'::jsonb);
        """
        return (sql, [uuid.uuid4(), target_tenant_id])

    await assert_table_tenant_isolation(
        table_name="audit_logs",
        company_a_tenant_id=ctx.company_a.tenant_id,
        company_b_tenant_id=ctx.company_b.tenant_id,
        company_a_row_id=id_a,
        company_b_row_id=id_b,
        insert_sql_factory=audit_insert_factory,
    )


@pytest.mark.asyncio
async def test_failed_operation_rolls_back_audit_entry(
    isolation_context: MultiTenantTestContext,
) -> None:
    """6. Verifies that failed business operations roll back audit logs (zero false-positive logs)."""
    ctx = isolation_context
    aborted_req_id = f"aborted-req-{uuid.uuid4().hex[:8]}"

    # Transaction begins, enqueues audit, but encounters an error and rolls back
    try:
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, ctx.company_a.tenant_id)
            await write_audit(
                session,
                action="payroll.disburse",
                target_type="payroll",
                target_id="batch_01",
                details={"amount": 500000},
                tenant_id=ctx.company_a.tenant_id,
                request_id=aborted_req_id,
            )
            # Simulate a business logic failure
            raise RuntimeError("Disbursement failed: insufficient funds.")
    except RuntimeError:
        pass

    # Verify in a new session that the audit record DOES NOT exist
    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            row = await conn.fetchrow(
                "SELECT * FROM public.audit_logs WHERE request_id = $1;", aborted_req_id
            )
            assert row is None, "CRITICAL: Aborted transaction left a committed audit entry!"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_successful_mutation_produces_consistent_audit_record(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """7. Verifies that a successful API mutation (membership update) commits a consistent audit record."""
    ctx = isolation_context
    target_membership_id = ctx.company_a.employee.membership_id
    url = f"/api/v1/auth/memberships/{target_membership_id}"
    headers = {
        "Authorization": f"Bearer {ctx.company_a.owner.token}",
        "X-Request-ID": f"audit-verif-{uuid.uuid4().hex[:8]}",
    }
    payload = {"is_payroll_admin": True}

    try:
        # 1. Execute mutation
        resp = await async_client.patch(url, headers=headers, json=payload)
        assert resp.status_code == 200
        req_id = resp.headers["X-Request-ID"]

        # 2. Inspect audit logs via read-only endpoint
        resp_logs = await async_client.get(
            "/api/v1/audit/logs", headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"}
        )
        assert resp_logs.status_code == 200
        logs = resp_logs.json()
        assert len(logs) > 0

        matching = [l for l in logs if l.get("request_id") == req_id]
        assert len(matching) == 1, (
            f"Expected 1 audit entry for request {req_id}, found {len(matching)}"
        )

        audit_entry = matching[0]
        assert audit_entry["action"] == "membership.update"
        assert audit_entry["target_type"] == "membership"
        assert audit_entry["target_id"] == target_membership_id
        assert audit_entry["actor_id"] == ctx.company_a.owner.user_id
        assert audit_entry["tenant_id"] == ctx.company_a.tenant_id
        assert audit_entry["details"]["updated"]["is_payroll_admin"] is True
    finally:
        # Restore baseline state so other test suites (e.g. test_clerk_auth.py) are unaffected
        await async_client.patch(url, headers=headers, json={"is_payroll_admin": False})


@pytest.mark.asyncio
async def test_audit_sanitization_cnic_and_credentials(
    isolation_context: MultiTenantTestContext,
) -> None:
    """8. Verifies that passwords, API keys, tokens, and Pakistani CNICs are strictly sanitized."""
    ctx = isolation_context

    dirty_payload = {
        "user_email": "candidate@example.com",
        "password": "UltraSecretPassword123!",
        "api_key": "sk_live_1234567890abcdef",
        "access_token": "eyJhbGciOi...",
        "profile": {
            "full_name": "Muhammad Ali",
            "cnic": "35202-1234567-1",  # Formatted Pakistani CNIC
            "raw_cnic": "3520212345671",  # 13-digit unformatted Pakistani CNIC
            "notes": "Candidate CNIC 42101-9876543-2 verified during interview.",
            "bearer_log": "Client invoked with Bearer secret_runtime_token_here in header.",
            "authorization": "Bearer secret_runtime_token_here",
        },
    }

    # Verify unit-level sanitizer
    cleaned = sanitize_value(dirty_payload)
    assert cleaned["password"] == "[REDACTED_SECRET]"
    assert cleaned["api_key"] == "[REDACTED_SECRET]"
    assert cleaned["access_token"] == "[REDACTED_SECRET]"
    assert cleaned["profile"]["cnic"] == "[REDACTED_CNIC]"
    assert cleaned["profile"]["raw_cnic"] == "[REDACTED_CNIC]"
    assert "[REDACTED_CNIC]" in cleaned["profile"]["notes"]
    assert "42101-9876543-2" not in cleaned["profile"]["notes"]
    assert "Bearer [REDACTED_TOKEN]" in cleaned["profile"]["bearer_log"]
    assert "secret_runtime_token_here" not in cleaned["profile"]["bearer_log"]
    assert cleaned["profile"]["authorization"] == "[REDACTED_SECRET]"

    # Verify persistence level
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id)
        entry = await write_audit(
            session,
            action="candidate.verify",
            target_type="candidate",
            target_id="cand_99",
            details=dirty_payload,
            tenant_id=ctx.company_a.tenant_id,
        )
        await session.commit()
        audit_id = entry.id

    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            row = await conn.fetchrow(
                "SELECT details FROM public.audit_logs WHERE id = $1::uuid;", audit_id
            )
            assert row is not None
            persisted_str = str(row["details"])
            assert "UltraSecretPassword123!" not in persisted_str
            assert "sk_live_1234567890abcdef" not in persisted_str
            assert "35202-1234567-1" not in persisted_str
            assert "3520212345671" not in persisted_str
            assert "42101-9876543-2" not in persisted_str
            assert "secret_runtime_token_here" not in persisted_str
            assert "[REDACTED_CNIC]" in persisted_str
            assert "[REDACTED_SECRET]" in persisted_str
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sample_synthetic_audit_records_display(
    isolation_context: MultiTenantTestContext,
) -> None:
    """9. Displays sample synthetic audit records as required by Stage 2 Phase 5."""
    ctx = isolation_context

    # Create two synthetic audit actions
    async with AsyncSessionLocal() as session:
        await set_tenant_context(
            session, ctx.company_a.tenant_id, ctx.company_a.owner.clerk_user_id
        )
        await write_audit(
            session,
            action="cv.ocr_verified",
            target_type="document",
            target_id=str(uuid.uuid4()),
            details={
                "agent": "DocuVerify",
                "status": "validated",
                "pages": 3,
                "confidence": 0.96,
            },
            actor_id=ctx.company_a.owner.user_id,
            tenant_id=ctx.company_a.tenant_id,
        )
        await write_audit(
            session,
            action="screener.ranked",
            target_type="candidate",
            target_id=str(uuid.uuid4()),
            details={
                "agent": "Screener",
                "score": 92,
                "rubric": "Senior Backend Engineer",
                "recommendation": "Advance to Code Tester",
            },
            actor_id=ctx.company_a.owner.user_id,
            tenant_id=ctx.company_a.tenant_id,
        )
        await session.commit()

    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);", ctx.company_a.tenant_id
            )
            rows = await conn.fetch("""
                SELECT id, action, target_type, target_id, timestamp, details, request_id
                FROM public.audit_logs
                ORDER BY timestamp DESC
                LIMIT 3;
            """)
            assert len(rows) > 0

            print("\n" + "=" * 60)
            print("SAMPLE SYNTHETIC AUDIT RECORDS (Acme Tech Solutions):")
            print("=" * 60)
            for r in rows:
                print(f"ID:          {r['id']}")
                print(f"Action:      {r['action']}")
                print(f"Target:      {r['target_type']} ({r['target_id']})")
                print(f"Timestamp:   {r['timestamp']}")
                print(f"Request ID:  {r['request_id']}")
                print(f"Details:     {r['details']}")
                print("-" * 60)
    finally:
        await conn.close()
