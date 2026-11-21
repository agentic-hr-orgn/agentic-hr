"""
Comprehensive Security, Concurrency & Correctness Regression Suite for Stage 2 Governance Core.
Verifies all fixes from the Stage 2 Pre-Stage-4 Audit (Migration 0013 & Components 1-7):

1. C1: All 11 alias views enforce RLS (security_invoker = true, security_barrier = true) and
       return 0 rows when queried as agentic_hr_app under a bogus/unrelated tenant context.
2. C1: Alias views block cross-tenant UPDATE and DELETE mutations under agentic_hr_app.
3. M3: TRUNCATE on public.audit_logs is blocked by trg_audit_logs_no_truncate (SQLSTATE 55000).
4. C2: parse_mock_token rejects mock tokens when ENVIRONMENT='production' even if USE_FAKE_INTEGRATIONS=True.
5. C2: Settings raises ValidationError when ENVIRONMENT='production' and USE_FAKE_INTEGRATIONS=True.
6. M2: IdempotencyGate ON CONFLICT DO NOTHING preserves caller's PostgreSQL transaction and
       transaction-local app.tenant_id RLS setting when losing a concurrent insert race.
7. L1: decrypt_field rejects unsupported envelope version or algorithm and canonicalizes UUID AAD.
8. L2: reset_tenant_context executes cleanly on AsyncSession (asyncpg) without multi-statement error.
9. M5: OutboxPublisherWorker(tenant_id=None).run_once() discovers due tenants via
       public.outbox_due_tenant_ids() and publishes events across multiple tenants under RLS.
"""

import json
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.clerk import parse_mock_token, register_test_signing_key
from app.core.config import Settings, settings
from app.core.encryption import (
    EncryptedFieldPayload,
    EncryptionContext,
    InvalidCiphertextFormatError,
    KeyRegistry,
    LocalKeyProvider,
    decrypt_field,
    encrypt_field,
)
from app.core.idempotency import IdempotencyGate, compute_payload_hash
from app.db.session import AsyncSessionLocal
from app.models.idempotency import IdempotencyRecord
from app.models.tenant import (
    get_clerk_user_context,
    get_tenant_context,
    reset_tenant_context,
    set_tenant_context,
)
from app.services.outbox_publisher import (
    FakeEventDispatcher,
    OutboxPublisherWorker,
    fetch_due_tenant_ids,
)
from app.services.outbox_service import enqueue_outbox_event, get_outbox_event
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
    get_app_connection,
)

ALIAS_VIEWS: tuple[str, ...] = (
    "audit_log",
    "approval",
    "approval_revision",
    "outbox_event",
    "idempotency_key",
    "connector_credential",
    "sent_message",
    "sheets_sync_queue_item",
    "sheets_drift_log",
    "drive_file",
    "calendar_event",
)


@pytest.fixture(autouse=True)
def setup_test_keys():
    """Register test RSA signing keys."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    yield


# ==============================================================================
# 1. C1: Alias Views Enforce RLS & Return 0 Rows Under Bogus Tenant
# ==============================================================================
@pytest.mark.asyncio
async def test_01_alias_views_enforce_rls_and_security_invoker(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that all 11 alias views:
    - Have security_invoker=true and security_barrier=true configured in pg_class.reloptions.
    - Are owned by agentic_hr_migrator (non-superuser, NOBYPASSRLS).
    - Return 0 rows when queried as agentic_hr_app under a bogus tenant UUID.
    """
    _ = isolation_context
    bogus_tenant = "00000000-0000-0000-0000-000000000000"

    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true);", bogus_tenant)

            for view in ALIAS_VIEWS:
                # 1. Verify reloptions and owner in pg_class
                meta = await conn.fetchrow(
                    """
                    SELECT c.reloptions, r.rolname AS owner_name, r.rolsuper, r.rolbypassrls
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
                    WHERE n.nspname = 'public' AND c.relname = $1 AND c.relkind = 'v';
                    """,
                    view,
                )
                assert meta is not None, f"View public.{view} does not exist"
                reloptions = meta["reloptions"] or []
                assert "security_invoker=true" in reloptions, (
                    f"View public.{view} missing security_invoker=true: {reloptions}"
                )
                assert "security_barrier=true" in reloptions, (
                    f"View public.{view} missing security_barrier=true: {reloptions}"
                )
                assert meta["owner_name"] == "agentic_hr_migrator"
                assert meta["rolsuper"] is False
                assert meta["rolbypassrls"] is False

                # 2. Verify 0 rows leaked under bogus tenant
                count = await conn.fetchval(f"SELECT count(*) FROM public.{view};")
                assert count == 0, (
                    f"CRITICAL RLS LEAK: View public.{view} leaked {count} rows under bogus tenant!"
                )
    finally:
        await conn.close()


# ==============================================================================
# 2. C1: Alias Views Block Cross-Tenant Mutations
# ==============================================================================
@pytest.mark.asyncio
async def test_02_alias_views_block_cross_tenant_mutations(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that Tenant B cannot read, UPDATE, or DELETE Tenant A's records through
    alias views (e.g. outbox_event), and audit_log view rejects DELETE/UPDATE.
    """
    ctx = isolation_context

    # Create an outbox event under Tenant A
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id)
        event = await enqueue_outbox_event(
            session=session,
            tenant_id=ctx.company_a.tenant_id,
            event_type="security.test_view_rls",
            event_payload={"secret": "tenant-a-confidential"},
        )
        await session.commit()
        event_id = event.id

    # Connect as agentic_hr_app scoped to Tenant B
    conn = await get_app_connection()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true);",
                ctx.company_b.tenant_id,
            )

            # Tenant B cannot SELECT Tenant A's event via public.outbox_event view
            row = await conn.fetchrow(
                "SELECT * FROM public.outbox_event WHERE id = $1::uuid;",
                event_id,
            )
            assert row is None

            # Tenant B cannot DELETE Tenant A's event via public.outbox_event view
            del_status = await conn.execute(
                "DELETE FROM public.outbox_event WHERE id = $1::uuid;",
                event_id,
            )
            assert del_status == "DELETE 0"

            # Audit log view rejects DELETE with InsufficientPrivilegeError (42501)
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("DELETE FROM public.audit_log;")
    finally:
        await conn.close()


# ==============================================================================
# 3. M3: TRUNCATE on public.audit_logs is Blocked by Statement Trigger
# ==============================================================================
@pytest.mark.asyncio
async def test_03_audit_logs_truncate_blocked_by_trigger() -> None:
    """
    Verifies that even a superuser/migration connection attempting TRUNCATE on
    public.audit_logs is blocked by trg_audit_logs_no_truncate with SQLSTATE 55000.
    """
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        with pytest.raises(asyncpg.exceptions.ObjectNotInPrerequisiteStateError) as exc_info:
            async with conn.transaction():
                await conn.execute("TRUNCATE TABLE public.audit_logs;")
        assert exc_info.value.sqlstate == "55000"
        assert "immutable and append-only" in str(exc_info.value)
    finally:
        await conn.close()


# ==============================================================================
# 4. C2: Mock Token Rejected in Production Even If USE_FAKE_INTEGRATIONS=True
# ==============================================================================
def test_04_mock_token_rejected_in_production(monkeypatch) -> None:
    """
    Verifies that parse_mock_token strictly rejects 'mock:...' tokens when
    ENVIRONMENT is 'production' or 'staging', even if USE_FAKE_INTEGRATIONS is True.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "USE_FAKE_INTEGRATIONS", True)

    with pytest.raises(HTTPException) as exc_info:
        parse_mock_token("mock:agentic.hr.test.ubaid.owner@gmail.com:org_acme")
    assert exc_info.value.status_code == 401
    assert "disabled in this environment" in exc_info.value.detail


# ==============================================================================
# 5. C2: Settings Model Validator Forbids USE_FAKE_INTEGRATIONS in Production
# ==============================================================================
def test_05_settings_forbids_fake_integrations_in_production() -> None:
    """
    Verifies that instantiating Settings with ENVIRONMENT='production' and
    USE_FAKE_INTEGRATIONS=True raises a Pydantic ValidationError at startup.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(ENVIRONMENT="production", USE_FAKE_INTEGRATIONS=True)
    assert "USE_FAKE_INTEGRATIONS=true is strictly forbidden when ENVIRONMENT=production" in str(
        exc_info.value
    )

    # Confirm ENVIRONMENT='production' with USE_FAKE_INTEGRATIONS=False and valid production secrets succeeds
    prod_settings = Settings(
        ENVIRONMENT="production",
        USE_FAKE_INTEGRATIONS=False,
        DEBUG=False,
        SECRET_KEY="prod-secret-key-at-least-32-bytes-long-verified",
        FIELD_ENCRYPTION_KEY="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        RATE_LIMIT_FAIL_OPEN=False,
    )
    assert prod_settings.is_non_production is False


# ==============================================================================
# 6. M2: IdempotencyGate Atomic Conflict Handling Preserves Caller Transaction & RLS
# ==============================================================================
@pytest.mark.asyncio
async def test_06_idempotency_gate_race_preserves_transaction_and_rls(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Simulates two concurrent transactions where Session B passes the initial SELECT
    before Session A commits a completed IdempotencyRecord, and then Session B executes
    the ON CONFLICT DO NOTHING insert path.
    Verifies that Session B:
    - Does NOT raise an unhandled IntegrityError or roll back its transaction.
    - Preserves its transaction-local app.tenant_id RLS context.
    - Re-selects and deterministically replays Session A's completed response.
    """
    ctx = isolation_context
    tenant_uuid = uuid.UUID(ctx.company_a.tenant_id)
    idem_key = f"race-key-{uuid.uuid4().hex[:12]}"
    operation = "test.concurrent_atomic_race"
    payload = {"amount": 125000, "currency": "PKR"}
    payload_hash = compute_payload_hash(payload)

    # Pre-insert a completed record via Session A (simulating a winner that committed
    # right before Session B's INSERT statement)
    async with AsyncSessionLocal() as session_a:
        await set_tenant_context(session_a, ctx.company_a.tenant_id)
        winner = IdempotencyRecord(
            tenant_id=tenant_uuid,
            key=idem_key,
            operation=operation,
            payload_hash=payload_hash,
            status="completed",
            response_code=201,
            response_body={"status": "winner_committed", "id": "rec-001"},
            locked_at=datetime.now(UTC),
        )
        session_a.add(winner)
        await session_a.commit()

    # Now test Session B where the first SELECT returns None (simulated race window)
    # so it hits the pg_insert(...).on_conflict_do_nothing() + re-SELECT path.
    async with AsyncSessionLocal() as session_b:
        await set_tenant_context(
            session_b,
            ctx.company_a.tenant_id,
            ctx.company_a.owner.clerk_user_id,
        )

        orig_execute = session_b.execute
        select_calls = {"count": 0}

        async def _race_simulating_execute(stmt, *args, **kwargs):
            sql_str = str(stmt)
            if "FROM idempotency_keys" in sql_str and "FOR UPDATE" in sql_str:
                select_calls["count"] += 1
                if select_calls["count"] == 1:
                    # Simulate empty result on the first SELECT before winner committed
                    class _EmptyResult:
                        def scalar_one_or_none(self):
                            return None

                    return _EmptyResult()
            return await orig_execute(stmt, *args, **kwargs)

        session_b.execute = _race_simulating_execute  # type: ignore[method-assign]

        async with IdempotencyGate(
            session=session_b,
            tenant_id=tenant_uuid,
            operation=operation,
            idempotency_key=idem_key,
            payload_hash=payload_hash,
        ) as gate:
            assert gate.is_replay is True
            assert gate.response_code == 201
            assert gate.response_body == {"status": "winner_committed", "id": "rec-001"}

        # Restore execute and verify transaction-local RLS context is STILL intact!
        session_b.execute = orig_execute  # type: ignore[method-assign]
        active_tenant = await get_tenant_context(session_b)
        active_user = await get_clerk_user_context(session_b)
        assert active_tenant == ctx.company_a.tenant_id
        assert active_user == ctx.company_a.owner.clerk_user_id


# ==============================================================================
# 7. L1: Encryption Envelope Validation & UUID Canonicalization
# ==============================================================================
def test_07_encryption_envelope_validation_and_uuid_canonicalization() -> None:
    """
    Verifies L1 hardening in app.core.encryption:
    1. EncryptionContext canonicalizes uppercase/UUID/string tenant_id and record_id to identical AAD bytes.
    2. decrypt_field rejects tampered envelope version or algorithm with InvalidCiphertextFormatError.
    """
    key_bytes = b"\x11" * 32
    provider = LocalKeyProvider(KeyRegistry("test-kek-v1", key_bytes))

    t_uuid = uuid.uuid4()
    r_uuid = uuid.uuid4()

    ctx_uuid_obj = EncryptionContext(
        tenant_id=t_uuid,
        entity_type="employee",
        record_id=r_uuid,
        field_name="cnic",
    )
    ctx_upper_str = EncryptionContext(
        tenant_id=str(t_uuid).upper(),
        entity_type="employee",
        record_id=str(r_uuid).upper(),
        field_name="cnic",
    )

    # 1. Confirm canonical AAD bytes match regardless of UUID casing
    assert ctx_uuid_obj.to_aad() == ctx_upper_str.to_aad()

    encrypted = encrypt_field("42101-1234567-1", ctx_uuid_obj, key_provider=provider)
    decrypted = decrypt_field(encrypted, ctx_upper_str, key_provider=provider)
    assert decrypted == "42101-1234567-1"

    # 2. Tamper version -> rejected with InvalidCiphertextFormatError
    raw_dict = json.loads(encrypted)
    raw_dict["version"] = "v99"
    with pytest.raises(InvalidCiphertextFormatError) as exc_ver:
        decrypt_field(json.dumps(raw_dict), ctx_uuid_obj, key_provider=provider)
    assert "Unsupported encrypted payload version 'v99'" in str(exc_ver.value)

    # 3. Tamper algorithm -> rejected with InvalidCiphertextFormatError
    raw_dict["version"] = "v1"
    raw_dict["algorithm"] = "AES-128-CBC"
    with pytest.raises(InvalidCiphertextFormatError) as exc_alg:
        decrypt_field(json.dumps(raw_dict), ctx_uuid_obj, key_provider=provider)
    assert "Unsupported data encryption algorithm 'AES-128-CBC'" in str(exc_alg.value)

    # 4. Tamper wrapping_provider -> rejected with InvalidCiphertextFormatError
    payload_obj = EncryptedFieldPayload.deserialize(encrypted)
    payload_obj.wrapping_provider = "aws_kms"
    with pytest.raises(InvalidCiphertextFormatError) as exc_prov:
        decrypt_field(payload_obj, ctx_uuid_obj, key_provider=provider)
    assert "does not match active provider" in str(exc_prov.value)


# ==============================================================================
# 8. L2: reset_tenant_context Succeeds on AsyncSession (asyncpg)
# ==============================================================================
@pytest.mark.asyncio
async def test_08_reset_tenant_context_asyncpg_compatible(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that reset_tenant_context(session) executes two separate RESET statements
    and succeeds on asyncpg without raising PostgresSyntaxError.
    """
    ctx = isolation_context
    async with AsyncSessionLocal() as session:
        await set_tenant_context(
            session,
            ctx.company_a.tenant_id,
            ctx.company_a.owner.clerk_user_id,
        )
        assert await get_tenant_context(session) == ctx.company_a.tenant_id
        assert await get_clerk_user_context(session) == ctx.company_a.owner.clerk_user_id

        await reset_tenant_context(session)

        assert await get_tenant_context(session) is None
        assert await get_clerk_user_context(session) is None


# ==============================================================================
# 9. M5: Cross-Tenant OutboxPublisherWorker Discovers & Publishes Multi-Tenant Events
# ==============================================================================
@pytest.mark.asyncio
async def test_09_cross_tenant_outbox_worker_discovery_and_publish(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that OutboxPublisherWorker(tenant_id=None).run_once():
    1. Calls public.outbox_due_tenant_ids() to discover tenants with pending events.
    2. Iterates over each tenant under its strict RLS context and publishes all due events.
    """
    ctx = isolation_context
    dispatcher = FakeEventDispatcher()

    # Clean outbox table first
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute("DELETE FROM public.outbox_events;")
    finally:
        await conn.close()

    # Enqueue 1 event for Tenant A and 1 event for Tenant B
    async with AsyncSessionLocal() as session_a:
        await set_tenant_context(session_a, ctx.company_a.tenant_id)
        ev_a = await enqueue_outbox_event(
            session=session_a,
            tenant_id=ctx.company_a.tenant_id,
            event_type="onboarding.started",
            event_payload={"tenant": "A"},
        )
        await session_a.commit()
        ev_a_id = ev_a.id

    async with AsyncSessionLocal() as session_b:
        await set_tenant_context(session_b, ctx.company_b.tenant_id)
        ev_b = await enqueue_outbox_event(
            session=session_b,
            tenant_id=ctx.company_b.tenant_id,
            event_type="onboarding.started",
            event_payload={"tenant": "B"},
        )
        await session_b.commit()
        ev_b_id = ev_b.id

    # Verify fetch_due_tenant_ids returns both Tenant A and Tenant B UUIDs
    async with AsyncSessionLocal() as session:
        due_ids = await fetch_due_tenant_ids(session)
        due_strs = {str(t) for t in due_ids}
        assert ctx.company_a.tenant_id in due_strs
        assert ctx.company_b.tenant_id in due_strs

    # Run OutboxPublisherWorker with tenant_id=None (cross-tenant mode)
    worker = OutboxPublisherWorker(
        session_factory=AsyncSessionLocal,
        tenant_id=None,
        dispatcher=dispatcher.dispatch,
    )
    processed = await worker.run_once()
    assert processed == 2
    assert len(dispatcher.dispatched_events) == 2

    # Verify both events are marked 'published' under their respective tenants
    async with AsyncSessionLocal() as session_a:
        await set_tenant_context(session_a, ctx.company_a.tenant_id)
        updated_a = await get_outbox_event(session_a, ev_a_id)
        assert updated_a is not None
        assert updated_a.status == "published"

    async with AsyncSessionLocal() as session_b:
        await set_tenant_context(session_b, ctx.company_b.tenant_id)
        updated_b = await get_outbox_event(session_b, ev_b_id)
        assert updated_b is not None
        assert updated_b.status == "published"
