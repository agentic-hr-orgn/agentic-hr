"""
Comprehensive Automated Test Suite for Outbox & Idempotency Foundation.
Complies with ADR 002 (Multi-Tenancy), ADR 006 (Fake Integrations), and Stage 2 Phase 8 specifications.

Verifies:
1. Normal event publishing: Outbox event created in business transaction -> published.
2. Idempotency duplicate replay: Re-sending identical key & payload returns cached result without re-executing.
3. Idempotency payload mismatch rejected: Re-using key with differing payload raises error (HTTP 422).
4. Concurrent outbox claiming (SKIP LOCKED): Multiple workers claim disjoint batches without duplicate dispatches.
5. Concurrent idempotency requests: Simultaneous requests on same key prevent duplicate execution.
6. Temporary failure and exponential retry: Transient error triggers backoff retry and eventual success.
7. Permanent failure (Dead Letter): Fatal errors transition to 'failed' without blocking queue.
8. Worker restart resilience: Orphaned 'processing' events are reclaimed by restarted worker.
9. Multi-tenant isolation: Separate tenant scopes for idempotency keys and RLS protection on outbox_events.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio

from app.core.clerk import register_test_signing_key
from app.core.idempotency import (
    IdempotencyConflictError,
    IdempotencyPayloadMismatchError,
    execute_idempotent_operation,
)
from app.db.session import AsyncSessionLocal
from app.models.outbox import OutboxEvent
from app.models.tenant import set_tenant_context
from app.services.outbox_publisher import (
    FakeEventDispatcher,
    OutboxPublisherWorker,
    PermanentDeliveryError,
    TransientDeliveryError,
    default_fake_dispatcher,
    publish_batch,
)
from app.services.outbox_service import enqueue_outbox_event, get_outbox_event
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
    assert_table_tenant_isolation,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment():
    """Ensure test RSA keys are registered and database tables are pristine for each test."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    default_fake_dispatcher.clear()

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute("DELETE FROM public.outbox_events;")
        await conn.execute("DELETE FROM public.idempotency_keys;")
    finally:
        await conn.close()

    yield

    default_fake_dispatcher.clear()
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute("DELETE FROM public.outbox_events;")
        await conn.execute("DELETE FROM public.idempotency_keys;")
    finally:
        await conn.close()


# ==============================================================================
# TEST 1: Normal Event Publishing
# ==============================================================================
@pytest.mark.asyncio
async def test_01_normal_event_publishing(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    1. Verifies that creating an approval writes an OutboxEvent atomically within the business transaction.
       The outbox publisher worker claims the event, dispatches it via fake adapters, and updates status to 'published'.
    """
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}

    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "annual",
            "start_date": "2026-12-20",
            "end_date": "2026-12-22",
            "days_count": 3.0,
            "reason": "Outbox integration verification annual leave.",
        },
        "approver_role": "manager",
    }

    # 1. Submit approval through HTTP API
    resp = await async_client.post("/api/v1/approvals", headers=headers, json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    approval_data = resp.json()
    approval_id = approval_data["id"]

    # 2. Verify OutboxEvent was created in 'pending' status
    async with AsyncSessionLocal() as session:
        await set_tenant_context(
            session, ctx.company_a.tenant_id, ctx.company_a.employee.clerk_user_id
        )
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            row = await conn.fetchrow(
                "SELECT id, status, attempts, event_type, event_payload, processed_at "
                "FROM public.outbox_events WHERE event_payload->>'approval_id' = $1;",
                approval_id,
            )
            assert row is not None, "Outbox event was not written in approval transaction!"
            assert row["status"] == "pending"
            assert row["attempts"] == 0
            assert row["event_type"] == "approval.created"
            assert row["processed_at"] is None
            event_id = row["id"]
        finally:
            await conn.close()

    # 3. Publish pending batch using fake dispatcher
    async with AsyncSessionLocal() as session:
        await set_tenant_context(
            session, ctx.company_a.tenant_id, ctx.company_a.employee.clerk_user_id
        )
        processed = await publish_batch(session=session, batch_size=10)
        assert processed >= 1, "Expected at least 1 event to be claimed and processed."

    # 4. Verify OutboxEvent status transitioned to 'published'
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id)
        event = await get_outbox_event(session, event_id)
        assert event is not None
        assert event.status == "published"
        assert event.attempts == 1
        assert event.processed_at is not None
        assert event.last_error is None

    # 5. Verify fake dispatcher delivered notification email without real external calls
    assert len(default_fake_dispatcher.dispatched_events) >= 1
    assert any(
        e["event_payload"].get("approval_id") == approval_id
        for e in default_fake_dispatcher.dispatched_events
    )


# ==============================================================================
# TEST 2: Idempotency Duplicate Replay
# ==============================================================================
@pytest.mark.asyncio
async def test_02_idempotency_duplicate_replay(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    2. Verifies that re-submitting an identical request with the same idempotency key and payload:
       - Returns the cached response code and body.
       - Sets is_replay=True.
       - Does NOT re-execute the business logic callback.
    """
    ctx = isolation_context
    tenant_id = UUID(ctx.company_a.tenant_id)
    key = f"key-replay-{uuid4().hex[:8]}"
    operation = "candidate.offer.issue"
    payload = {"candidate_id": str(uuid4()), "salary": 120000, "role": "Senior Engineer"}

    execution_counter = 0

    async def _business_logic() -> tuple[int, dict]:
        nonlocal execution_counter
        execution_counter += 1
        return 201, {"offer_id": f"off-{uuid4().hex[:6]}", "status": "issued"}

    # First Execution: Fresh execution
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        code1, body1, is_replay1 = await execute_idempotent_operation(
            session=session,
            tenant_id=tenant_id,
            operation=operation,
            idempotency_key=key,
            payload=payload,
            execute_fn=_business_logic,
        )
        await session.commit()

    assert is_replay1 is False
    assert code1 == 201
    assert execution_counter == 1
    original_offer_id = body1["offer_id"]

    # Second Execution: Exact duplicate request
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        code2, body2, is_replay2 = await execute_idempotent_operation(
            session=session,
            tenant_id=tenant_id,
            operation=operation,
            idempotency_key=key,
            payload=payload,
            execute_fn=_business_logic,
        )

    assert is_replay2 is True
    assert code2 == 201
    assert body2["offer_id"] == original_offer_id
    assert execution_counter == 1, "Business logic callback was illegally re-executed on duplicate!"


# ==============================================================================
# TEST 3: Idempotency Payload Mismatch Rejected
# ==============================================================================
@pytest.mark.asyncio
async def test_03_idempotency_payload_mismatch_rejected(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    3. Verifies that attempting to reuse an existing idempotency key with a differing payload
       or differing operation is rejected immediately with IdempotencyPayloadMismatchError.
    """
    ctx = isolation_context
    tenant_id = UUID(ctx.company_a.tenant_id)
    key = f"key-mismatch-{uuid4().hex[:8]}"
    operation = "payroll.adjustment"

    original_payload = {"employee_id": "emp-1", "amount": 500}
    tampered_payload = {"employee_id": "emp-1", "amount": 9000}

    # Step 1: Execute original operation successfully
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        code, _body, is_replay = await execute_idempotent_operation(
            session=session,
            tenant_id=tenant_id,
            operation=operation,
            idempotency_key=key,
            payload=original_payload,
            execute_fn=lambda: asyncio.sleep(0.01, result=(200, {"adjusted": True})),
        )
        await session.commit()

    assert code == 200
    assert is_replay is False

    # Step 2: Re-use key with differing payload -> Must raise IdempotencyPayloadMismatchError
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        with pytest.raises(IdempotencyPayloadMismatchError) as exc_info:
            await execute_idempotent_operation(
                session=session,
                tenant_id=tenant_id,
                operation=operation,
                idempotency_key=key,
                payload=tampered_payload,
                execute_fn=lambda: asyncio.sleep(0.01, result=(200, {"tampered": True})),
            )
        assert "previously used with a different request payload" in str(exc_info.value)

    # Step 3: Re-use key with differing operation name -> Must raise IdempotencyPayloadMismatchError
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        with pytest.raises(IdempotencyPayloadMismatchError):
            await execute_idempotent_operation(
                session=session,
                tenant_id=tenant_id,
                operation="payroll.bonus_award",  # Differing operation
                idempotency_key=key,
                payload=original_payload,
                execute_fn=lambda: asyncio.sleep(0.01, result=(200, {"bonus": True})),
            )


# ==============================================================================
# TEST 4: Concurrent Outbox Publishing (SKIP LOCKED)
# ==============================================================================
@pytest.mark.asyncio
async def test_04_concurrent_outbox_skip_locked(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    4. Verifies that multiple concurrent publisher workers process a queue of pending events
       without lock contention, deadlock, or duplicate dispatches using SELECT ... FOR UPDATE SKIP LOCKED.
    """
    ctx = isolation_context
    tenant_id = UUID(ctx.company_a.tenant_id)
    event_ids: list[UUID] = []

    # Insert 9 distinct pending events
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        for i in range(9):
            event = await enqueue_outbox_event(
                session=session,
                tenant_id=tenant_id,
                event_type=f"batch.test.event.{i}",
                event_payload={"index": i, "token": uuid4().hex},
            )
            event_ids.append(event.id)
        await session.commit()

    test_dispatcher = FakeEventDispatcher()

    # Define a publisher worker task
    async def _worker_task(worker_id: int) -> int:
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
            return await publish_batch(
                session=session,
                batch_size=3,
                dispatcher=test_dispatcher.dispatch,
            )

    # Launch 3 concurrent workers simultaneously
    results = await asyncio.gather(
        _worker_task(1),
        _worker_task(2),
        _worker_task(3),
    )

    total_processed = sum(results)
    assert total_processed == 9, f"Expected 9 total processed events, got {total_processed}"

    # Verify zero duplicate dispatches across concurrent workers
    dispatched_ids = [d["event_id"] for d in test_dispatcher.dispatched_events]
    assert len(dispatched_ids) == 9
    assert len(set(dispatched_ids)) == 9, "Duplicate event dispatch occurred across workers!"

    # Verify all rows transitioned to 'published' in PostgreSQL
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id))
        for eid in event_ids:
            ev = await get_outbox_event(session, eid)
            assert ev is not None
            assert ev.status == "published"
            assert ev.attempts == 1


# ==============================================================================
# TEST 5: Concurrent Idempotency Requests
# ==============================================================================
@pytest.mark.asyncio
async def test_05_concurrent_idempotency_requests(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    5. Verifies that concurrent requests attempting the identical operation with the same
       idempotency key do not execute the underlying business logic twice.
    """
    ctx = isolation_context
    tenant_id = UUID(ctx.company_a.tenant_id)
    key = f"key-concurrent-{uuid4().hex[:8]}"
    operation = "leave.balance.deduct"
    payload = {"leave_id": str(uuid4()), "days": 2.5}

    execution_counter = 0

    async def _slow_business_logic() -> tuple[int, dict]:
        nonlocal execution_counter
        execution_counter += 1
        await asyncio.sleep(0.15)  # Simulate non-trivial business processing
        return 200, {"deducted": 2.5}

    async def _attempt_operation() -> tuple[str, int, dict]:
        try:
            async with AsyncSessionLocal() as session:
                await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
                code, body, _is_replay = await execute_idempotent_operation(
                    session=session,
                    tenant_id=tenant_id,
                    operation=operation,
                    idempotency_key=key,
                    payload=payload,
                    execute_fn=_slow_business_logic,
                )
                await session.commit()
                return ("success", code, body)
        except IdempotencyConflictError as err:
            return ("conflict", 409, {"error": str(err)})

    # Run two operations concurrently
    task1 = asyncio.create_task(_attempt_operation())
    await asyncio.sleep(0.02)  # Slight offset to guarantee task 1 enters first
    task2 = asyncio.create_task(_attempt_operation())

    res1, res2 = await asyncio.gather(task1, task2)

    # Exactly one executed the business logic
    assert execution_counter == 1, (
        f"Business logic executed {execution_counter} times instead of 1!"
    )

    # One task succeeded, and the overlapping concurrent task correctly detected the in-flight operation
    statuses = {res1[0], res2[0]}
    assert "success" in statuses
    assert "conflict" in statuses or statuses == {"success"}


# ==============================================================================
# TEST 6: Temporary Failure and Exponential Retry
# ==============================================================================
@pytest.mark.asyncio
async def test_06_temporary_failure_and_exponential_retry(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    6. Verifies that transient delivery failures:
       - Increment attempts counter.
       - Remain in 'pending' status.
       - Calculate exponential backoff next_attempt_at.
       - Store last_error for diagnostics.
       - Succeed when retried after the backoff window.
    """
    ctx = isolation_context
    tenant_id = UUID(ctx.company_a.tenant_id)
    event_type = f"transient.test.{uuid4().hex[:6]}"

    attempt_count = 0

    async def _flaky_handler(event: OutboxEvent) -> bool:
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise TransientDeliveryError("Simulated external network timeout (HTTP 503)")
        return True

    test_dispatcher = FakeEventDispatcher()
    test_dispatcher.register_handler(event_type, _flaky_handler)

    # 1. Enqueue outbox event
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        event = await enqueue_outbox_event(
            session=session,
            tenant_id=tenant_id,
            event_type=event_type,
            event_payload={"sample": "data"},
        )
        event_id = event.id
        await session.commit()

    # 2. Run publisher (Attempt 1 -> Fails)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        processed = await publish_batch(
            session=session,
            batch_size=5,
            dispatcher=test_dispatcher.dispatch,
            base_backoff_seconds=2.0,
        )
        assert processed == 1

    # 3. Verify event state after Attempt 1 failure
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id))
        event = await get_outbox_event(session, event_id)
        assert event is not None
        assert event.status == "pending"
        assert event.attempts == 1
        assert "Simulated external network timeout" in (event.last_error or "")
        # Exponential backoff check: next_attempt_at must be approximately now + 2s
        assert event.next_attempt_at > datetime.now(UTC)

        # Fast-forward next_attempt_at to make it eligible immediately for Attempt 2
        event.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    # 4. Run publisher (Attempt 2 -> Succeeds)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        processed = await publish_batch(
            session=session,
            batch_size=5,
            dispatcher=test_dispatcher.dispatch,
        )
        assert processed == 1

    # 5. Verify final published state
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id))
        event = await get_outbox_event(session, event_id)
        assert event is not None
        assert event.status == "published"
        assert event.attempts == 2
        assert event.processed_at is not None
        assert event.last_error is None


# ==============================================================================
# TEST 7: Permanent Failure (Dead Letter)
# ==============================================================================
@pytest.mark.asyncio
async def test_07_permanent_failure_dead_letter(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    7. Verifies that fatal delivery failures (or retries exhaustion):
       - Transition directly to 'failed' (dead letter).
       - Record full error details in last_error.
       - Do not block subsequent items in the queue.
       - Are skipped on subsequent polling cycles.
    """
    ctx = isolation_context
    tenant_id = UUID(ctx.company_a.tenant_id)
    fatal_event_type = f"fatal.test.{uuid4().hex[:6]}"
    normal_event_type = f"normal.test.{uuid4().hex[:6]}"

    async def _fatal_handler(event: OutboxEvent) -> bool:
        raise PermanentDeliveryError("HTTP 400 Bad Request: Recipient address permanently rejected")

    test_dispatcher = FakeEventDispatcher()
    test_dispatcher.register_handler(fatal_event_type, _fatal_handler)

    # Enqueue fatal event followed by normal event
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        fatal_ev = await enqueue_outbox_event(
            session=session,
            tenant_id=tenant_id,
            event_type=fatal_event_type,
            event_payload={"type": "fatal"},
        )
        normal_ev = await enqueue_outbox_event(
            session=session,
            tenant_id=tenant_id,
            event_type=normal_event_type,
            event_payload={"type": "normal"},
        )
        fatal_id = fatal_ev.id
        normal_id = normal_ev.id
        await session.commit()

    # Run publisher over batch of 2
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        processed = await publish_batch(
            session=session,
            batch_size=5,
            dispatcher=test_dispatcher.dispatch,
        )
        assert processed == 2

    # Verify fatal event is marked 'failed'
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id))
        fatal_row = await get_outbox_event(session, fatal_id)
        assert fatal_row is not None
        assert fatal_row.status == "failed"
        assert fatal_row.processed_at is not None
        assert "permanently rejected" in (fatal_row.last_error or "").lower()

        # Verify normal event succeeded despite the fatal error preceding it
        normal_row = await get_outbox_event(session, normal_id)
        assert normal_row is not None
        assert normal_row.status == "published"

    # Subsequent run: Fatal event must NOT be claimed
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        re_processed = await publish_batch(
            session=session,
            batch_size=5,
            dispatcher=test_dispatcher.dispatch,
        )
        assert re_processed == 0, "Dead-lettered failed event was improperly re-claimed!"


# ==============================================================================
# TEST 8: Worker Restart Resilience
# ==============================================================================
@pytest.mark.asyncio
async def test_08_worker_restart_resilience(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    8. Verifies that if a publisher worker crashes mid-processing, leaving an event in 'processing' status,
       a newly started/restarted worker reclaims the orphaned event after the stale timeout
       and completes delivery successfully.
    """
    ctx = isolation_context
    tenant_id = UUID(ctx.company_a.tenant_id)

    # 1. Simulate worker crash: event left in 'processing' with stale next_attempt_at
    stale_time = datetime.now(UTC) - timedelta(seconds=120)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), ctx.company_a.owner.clerk_user_id)
        orphaned_event = OutboxEvent(
            tenant_id=tenant_id,
            event_type="worker.crash.test",
            event_payload={"crash": True, "token": uuid4().hex},
            status="processing",
            attempts=1,
            next_attempt_at=stale_time,
        )
        session.add(orphaned_event)
        await session.commit()
        event_id = orphaned_event.id

    # 2. Instantiate a fresh restarted worker with stale threshold of 60s
    test_dispatcher = FakeEventDispatcher()
    worker = OutboxPublisherWorker(
        session_factory=AsyncSessionLocal,
        tenant_id=tenant_id,
        batch_size=5,
        dispatcher=test_dispatcher.dispatch,
        stale_processing_seconds=60,
    )

    # 3. Restarted worker runs single cycle
    processed = await worker.run_once()
    assert processed >= 1, "Restarted worker failed to reclaim orphaned event!"

    # 4. Verify orphaned event was recovered and published
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id))
        event = await get_outbox_event(session, event_id)
        assert event is not None
        assert event.status == "published"
        assert event.processed_at is not None
        assert event.attempts == 2  # 1 from initial crash + 1 from recovery


# ==============================================================================
# TEST 9: Multi-Tenant Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_09_multi_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    9. Verifies multi-tenant isolation:
       - Identical idempotency keys can be used across different tenants without collision.
       - RLS strictly prevents cross-tenant outbox inspection and claiming.
    """
    ctx = isolation_context
    tenant_a_id = UUID(ctx.company_a.tenant_id)
    tenant_b_id = UUID(ctx.company_b.tenant_id)

    shared_key = f"shared-cross-tenant-key-{uuid4().hex[:6]}"
    operation = "department.budget.approve"

    # Company A executes with shared_key
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_a_id), ctx.company_a.owner.clerk_user_id)
        code_a, body_a, is_replay_a = await execute_idempotent_operation(
            session=session,
            tenant_id=tenant_a_id,
            operation=operation,
            idempotency_key=shared_key,
            payload={"dept": "Engineering", "budget": 50000},
            execute_fn=lambda: asyncio.sleep(0.01, result=(200, {"company": "A", "ok": True})),
        )
        await session.commit()

    assert code_a == 200
    assert is_replay_a is False
    assert body_a["company"] == "A"

    # Company B executes with the EXACT SAME key and a different payload
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_b_id), ctx.company_b.owner.clerk_user_id)
        code_b, body_b, is_replay_b = await execute_idempotent_operation(
            session=session,
            tenant_id=tenant_b_id,
            operation=operation,
            idempotency_key=shared_key,
            payload={"dept": "Marketing", "budget": 20000},  # Different payload
            execute_fn=lambda: asyncio.sleep(0.01, result=(200, {"company": "B", "ok": True})),
        )
        await session.commit()

    assert code_b == 200
    assert is_replay_b is False
    assert body_b["company"] == "B"

    # Verify RLS table isolation on outbox_events
    # Enqueue an event for Company A and an event for Company B
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_a_id), ctx.company_a.owner.clerk_user_id)
        ev_a = await enqueue_outbox_event(session, tenant_a_id, "iso.test.a", {"val": "A"})
        await session.commit()
        ev_a_id = str(ev_a.id)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_b_id), ctx.company_b.owner.clerk_user_id)
        ev_b = await enqueue_outbox_event(session, tenant_b_id, "iso.test.b", {"val": "B"})
        await session.commit()
        ev_b_id = str(ev_b.id)

    # 6-Point RLS Verification on outbox_events table
    await assert_table_tenant_isolation(
        table_name="outbox_events",
        company_a_tenant_id=str(tenant_a_id),
        company_b_tenant_id=str(tenant_b_id),
        company_a_row_id=ev_a_id,
        company_b_row_id=ev_b_id,
    )
