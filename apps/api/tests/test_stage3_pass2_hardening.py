"""
Stage 3 Pass 2 Hardening Regression Test Suite.
Verifies all backend fixes from the Stage 3 Pass 2 audit:
  T1: MCP 401 circuit-breaker persistence (get_mcp_session commits needs_reconnect, audit, idempotency failed).
  T2: POST /connectors/{id}/refresh 401/invalid_grant persists circuit-breaker trip and returns HTTP 401.
  T3: Global connector error mapping (ConnectorRateLimitError -> 429 + Retry-After, ConnectorServiceUnavailableError -> 503).
  T4: Sheets batch sync does not double-count rate limits or burn row retry_count on 429.
  T5: All 13 FastMCP tools carry @mcp_guardrail matching MUTATING_TOOLS / DEFAULT_ALLOWED_READ_TOOLS,
      and AST scan proves no unauthorized module bypasses execute_connector_call via app.integrations.factory.
  T6: Asia/Karachi (PKT, +05:00) normalization across Calendar create, idempotent replay, update fallback, and MCP outputs.
  T7: POST /connectors/{id}/reconnect returns 409 OAUTH_REAUTH_REQUIRED + authorization_url when USE_FAKE_INTEGRATIONS=false.
  T8: Direct Temporal activities & FakeEventDispatcher fail closed when USE_FAKE_INTEGRATIONS=false.
"""

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from app.core.clerk import register_test_signing_key
from app.core.config import settings
from app.core.timezones import KARACHI_TZ_NAME, get_karachi_tz, iso_pkt, to_karachi
from app.core.untrusted import DEFAULT_ALLOWED_READ_TOOLS, MUTATING_TOOLS
from app.db.session import AsyncSessionLocal
from app.integrations.factory import get_calendar_service
from app.mcp.calendar_mcp import create_calendar_event_tool
from app.mcp.context import MCPExecutionContext, mcp_execution_context
from app.mcp.gmail_mcp import send_email_tool
from app.mcp.server import ALL_MCP_TOOLS
from app.models.audit_log import AuditLog
from app.models.connector import ConnectorCredential
from app.models.idempotency import IdempotencyRecord
from app.models.outbox import OutboxEvent
from app.models.sheets import SheetsSyncQueue
from app.models.tenant import set_tenant_context
from app.services.calendar_service import CalendarSchedulingService
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)
from app.services.outbox_publisher import FakeEventDispatcher, PermanentDeliveryError
from app.services.sheets_sync_service import SheetsSyncService
from app.workflows.activities import send_welcome_notification
from tests.isolation_harness import (
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
)


@pytest.fixture(autouse=True)
def setup_test_keys():
    """Ensure test RSA keys are registered for offline token verification."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    yield


# ==============================================================================
# T1: MCP 401 Circuit-Breaker Persistence (A1)
# ==============================================================================
@pytest.mark.asyncio
async def test_t1_mcp_401_persists_circuit_breaker_and_audit(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that when an MCP tool encounters a 401 / invalid_grant, get_mcp_session()
    commits the circuit-breaker trip (needs_reconnect, jobs_paused=True), audit log,
    and idempotency 'failed' marker before re-raising ConnectorAuthExpiredError.
    """
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Connect a valid credential
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.mcp_valid_token",
            "refresh_token": "valid_refresh_token",
            "token_expiry": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]
    idem_key = f"mcp-401-idem-{uuid.uuid4().hex[:10]}"

    # 2. Invoke send_email_tool targeting simulate-401@test.com
    mcp_ctx = MCPExecutionContext(
        tenant_id=uuid.UUID(ctx.company_a.tenant_id),
        user_id=uuid.UUID(ctx.company_a.owner.user_id),
        role="owner",
        credential_id=uuid.UUID(cred_id),
    )

    with mcp_execution_context(mcp_ctx), pytest.raises(ConnectorAuthExpiredError):
        await send_email_tool(
            to_email="simulate-401@test.com",
            subject="MCP 401 Circuit-Breaker Persistence Test",
            body_text="Should trip breaker and persist to Postgres",
            idempotency_key=idem_key,
        )

    # 3. Verify in a FRESH database session that the trip survived!
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)

        cred = (
            await session.execute(
                select(ConnectorCredential).where(ConnectorCredential.id == uuid.UUID(cred_id))
            )
        ).scalar_one()
        assert cred.status == "needs_reconnect"
        assert cred.jobs_paused is True
        assert "401" in (cred.error_message or "")

        audit = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
                        AuditLog.action == "connector.needs_reconnect",
                        AuditLog.target_id == cred_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert audit is not None

        idem = (
            (
                await session.execute(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
                        IdempotencyRecord.key == idem_key,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert idem is not None
        assert idem.status == "failed"


# ==============================================================================
# T2: POST /connectors/{id}/refresh 401/invalid_grant Persists Trip (A2)
# ==============================================================================
@pytest.mark.asyncio
async def test_t2_refresh_endpoint_401_persists_circuit_breaker(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that POST /api/v1/connectors/{id}/refresh with a revoked/invalid_grant
    refresh token returns HTTP 401 CONNECTOR_AUTH_EXPIRED (not 500) and commits
    status='needs_reconnect' + jobs_paused=True in PostgreSQL.
    """
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.expired_token",
            "refresh_token": "invalid_grant_revoked_token",
            "token_expiry": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    refresh_resp = await async_client.post(
        f"/api/v1/connectors/{cred_id}/refresh",
        headers=headers,
    )
    assert refresh_resp.status_code == 401
    assert "CONNECTOR_AUTH_EXPIRED" in refresh_resp.json()["error"]["message"]

    # Verify DB persistence in a fresh session
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        cred = (
            await session.execute(
                select(ConnectorCredential).where(ConnectorCredential.id == uuid.UUID(cred_id))
            )
        ).scalar_one()
        assert cred.status == "needs_reconnect"
        assert cred.jobs_paused is True


# ==============================================================================
# T3: Global Connector Error Mapping (429 + Retry-After, 503) (A3)
# ==============================================================================
@pytest.mark.asyncio
async def test_t3_global_connector_error_handlers(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifies that ConnectorRateLimitError and ConnectorServiceUnavailableError raised
    from connector services map cleanly to HTTP 429 (with Retry-After) and HTTP 503.
    """
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    async def _raise_429(*_args, **_kwargs):
        raise ConnectorRateLimitError("Google quota exceeded", retry_after=17)

    monkeypatch.setattr(CalendarSchedulingService, "get_free_busy", _raise_429)
    resp_429 = await async_client.post(
        "/api/v1/connectors/calendar/free-busy",
        headers=headers,
        json={
            "calendar_ids": ["primary"],
            "time_min": datetime.now(UTC).isoformat(),
            "time_max": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
    )
    assert resp_429.status_code == 429
    assert resp_429.headers.get("retry-after") == "17"
    assert "CONNECTOR_RATE_LIMITED" in resp_429.json()["error"]["message"]

    async def _raise_503(*_args, **_kwargs):
        raise ConnectorServiceUnavailableError("Upstream Google Calendar 503")

    monkeypatch.setattr(CalendarSchedulingService, "get_free_busy", _raise_503)
    resp_503 = await async_client.post(
        "/api/v1/connectors/calendar/free-busy",
        headers=headers,
        json={
            "calendar_ids": ["primary"],
            "time_min": datetime.now(UTC).isoformat(),
            "time_max": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
    )
    assert resp_503.status_code == 503
    assert "CONNECTOR_UNAVAILABLE" in resp_503.json()["error"]["message"]


# ==============================================================================
# T4: Sheets Batch Sync Rate-Limit Double-Counting & 429 Protection (B1)
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_sheets_batch_no_double_counting_and_429_preserves_retries(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    1. Enqueues 40 rows and processes them in one batch (batch_size=40); confirms all 40
       succeed without hitting the 30/min batch rate-limit bucket.
    2. Forces an inner 429 HTTPException on a row and confirms the row is returned to
       'pending' with retry_count == 0 and batch status == 'rate_limited'.
    """
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Ensure active connected credential
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.sheets_batch_token",
            "refresh_token": "valid_refresh_sheets",
            "token_expiry": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
    )
    assert conn_resp.status_code == 200

    # 1. Enqueue 40 pending rows
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        for i in range(40):
            await SheetsSyncService.enqueue_sync(
                session=session,
                tenant_id=ctx.company_a.tenant_id,
                entity_type="employee",
                entity_id=f"EMP-BATCH-{i:03d}",
                action="upsert",
                payload={"employee_id": f"EMP-BATCH-{i:03d}", "name": f"Worker {i}"},
            )
        await session.commit()

    # Process all 40 in one batch
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        res = await SheetsSyncService.process_sync_queue(
            session=session,
            tenant_id=ctx.company_a.tenant_id,
            batch_size=40,
            actor_id=ctx.company_a.owner.user_id,
        )
        await session.commit()
        assert res.status == "completed"
        assert res.completed_count >= 40
        assert res.failed_count == 0

    # 2. Enqueue 1 row and simulate inner 429 from execute_connector_call
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        queued = await SheetsSyncService.enqueue_sync(
            session=session,
            tenant_id=ctx.company_a.tenant_id,
            entity_type="employee",
            entity_id="EMP-429-CHECK",
            action="upsert",
            payload={"employee_id": "EMP-429-CHECK", "name": "Rate Limited"},
        )
        await session.commit()
        queued_id = queued.id

    async def _force_429(*_args, **_kwargs):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": "10"},
        )

    import app.services.sheets_sync_service as sheets_mod

    monkeypatch.setattr(sheets_mod, "execute_connector_call", _force_429)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        res_rl = await SheetsSyncService.process_sync_queue(
            session=session,
            tenant_id=ctx.company_a.tenant_id,
            batch_size=10,
            actor_id=ctx.company_a.owner.user_id,
        )
        await session.commit()
        assert res_rl.status == "rate_limited"
        assert res_rl.failed_count == 0

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        row = (
            await session.execute(select(SheetsSyncQueue).where(SheetsSyncQueue.id == queued_id))
        ).scalar_one()
        assert row.status == "pending"
        assert row.retry_count == 0


# ==============================================================================
# T5: 100% Guardrail Coverage & AST Bypass Scan (B6)
# ==============================================================================
def test_t5_mcp_guardrail_coverage_and_ast_wrapper_scan() -> None:
    """
    1. Asserts all 13 MCP tools in ALL_MCP_TOOLS carry __mcp_guardrail__ metadata and
       match MUTATING_TOOLS vs DEFAULT_ALLOWED_READ_TOOLS.
    2. Scans every Python file under apps/api/app via AST and asserts only allowlisted
       modules import app.integrations.factory (preventing execute_connector_call bypasses).
    """
    assert len(ALL_MCP_TOOLS) == 13
    for tool_fn in ALL_MCP_TOOLS:
        meta = getattr(tool_fn, "__mcp_guardrail__", None)
        assert meta is not None, f"Tool '{tool_fn.__name__}' is missing @mcp_guardrail!"
        tool_name = meta["tool"]
        is_write = meta["is_write"]
        if is_write:
            assert tool_name in MUTATING_TOOLS, f"Mutating tool '{tool_name}' not in MUTATING_TOOLS"
        else:
            assert tool_name in DEFAULT_ALLOWED_READ_TOOLS, (
                f"Read tool '{tool_name}' not in DEFAULT_ALLOWED_READ_TOOLS"
            )

    app_root = Path(__file__).resolve().parent.parent / "app"
    allowed_factory_importers = {
        "integrations/__init__.py",
        "services/email_delivery_service.py",
        "services/sheets_sync_service.py",
        "services/drive_service.py",
        "services/calendar_service.py",
        "workflows/activities.py",
        "routers/synthetic.py",
    }

    violations: list[str] = []
    for py_file in app_root.rglob("*.py"):
        rel = py_file.relative_to(app_root).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.integrations.factory":
                if rel not in allowed_factory_importers:
                    violations.append(rel)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if (
                        alias.name == "app.integrations.factory"
                        and rel not in allowed_factory_importers
                    ):
                        violations.append(rel)

    assert not violations, f"Unauthorized app.integrations.factory imports: {violations}"


# ==============================================================================
# T6: Asia/Karachi Timezone Normalization Across Create, Replay, Update & MCP (B3, B4, B5)
# ==============================================================================
@pytest.mark.asyncio
async def test_t6_karachi_timezone_normalization(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies Asia/Karachi (+05:00) normalization:
    - Unit cases for to_karachi() and iso_pkt().
    - Calendar create -> idempotent replay -> MCP output all emit +05:00.
    - Calendar update_event fallback without timestamps produces aware PKT datetime.
    """
    # Unit cases
    utc_dt = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
    pkt_dt = to_karachi(utc_dt)
    assert pkt_dt.hour == 15
    assert pkt_dt.isoformat().endswith("+05:00")
    assert iso_pkt(utc_dt) == "2026-11-20T15:00:00+05:00"
    assert get_karachi_tz().utcoffset(pkt_dt) == timedelta(hours=5)

    naive_dt = datetime(2026, 11, 20, 14, 30, 0)
    assert to_karachi(naive_dt).isoformat() == "2026-11-20T14:30:00+05:00"

    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.pkt_test_token",
            "refresh_token": "valid_refresh_pkt",
            "token_expiry": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # Create event with UTC timestamp & check both create and idempotent replay serialize +05:00
    idem_key = f"cal-pkt-{uuid.uuid4().hex[:10]}"
    event_payload = {
        "title": "PKT Normalization Interview",
        "start_time": "2026-11-25T09:00:00Z",
        "end_time": "2026-11-25T10:00:00Z",
        "attendees": ["candidate@example.com"],
        "timezone": "UTC",
    }
    create_resp = await async_client.post(
        "/api/v1/connectors/calendar/events",
        headers={**headers, "Idempotency-Key": idem_key},
        json=event_payload,
    )
    assert create_resp.status_code == 200
    created_data = create_resp.json()
    assert created_data["start_time"].endswith("+05:00")
    assert created_data["end_time"].endswith("+05:00")
    assert created_data["created_at"].endswith("+05:00")
    assert created_data["timezone"] == KARACHI_TZ_NAME

    # Idempotent replay (loaded from Postgres asyncpg as UTC, normalized by KarachiDatetime)
    replay_resp = await async_client.post(
        "/api/v1/connectors/calendar/events",
        headers={**headers, "Idempotency-Key": idem_key},
        json=event_payload,
    )
    assert replay_resp.status_code == 200
    replay_data = replay_resp.json()
    assert replay_data["id"] == created_data["id"]
    assert replay_data["start_time"].endswith("+05:00")
    assert replay_data["end_time"].endswith("+05:00")

    # MCP create_calendar_event_tool returns +05:00 timestamps
    mcp_ctx = MCPExecutionContext(
        tenant_id=uuid.UUID(ctx.company_a.tenant_id),
        user_id=uuid.UUID(ctx.company_a.owner.user_id),
        role="owner",
        credential_id=uuid.UUID(cred_id),
    )
    with mcp_execution_context(mcp_ctx):
        mcp_res = await create_calendar_event_tool(
            title="MCP PKT Interview",
            start_time="2026-11-26T08:00:00Z",
            end_time="2026-11-26T09:00:00Z",
            attendees=["candidate@example.com"],
        )
        assert mcp_res["start_time"].endswith("+05:00")
        assert mcp_res["end_time"].endswith("+05:00")
        assert mcp_res["created_at"].endswith("+05:00")

    # Update untracked calendar event with no start_time/end_time -> aware PKT fallback
    fake_cal = get_calendar_service()
    untracked_id = f"untracked_{uuid.uuid4().hex[:8]}"
    if hasattr(fake_cal, "events"):
        fake_cal.events.append(
            {
                "id": untracked_id,
                "event_id": untracked_id,
                "title": "External Event",
                "start": "2026-11-27T10:00:00+05:00",
                "end": "2026-11-27T11:00:00+05:00",
                "attendees": [],
                "status": "confirmed",
            }
        )
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        updated = await CalendarSchedulingService.update_event(
            session=session,
            tenant_id=ctx.company_a.tenant_id,
            event_id=untracked_id,
            title="Updated Title Only",
            credential_id=cred_id,
            actor_id=ctx.company_a.owner.user_id,
        )
        assert updated.start_time.tzinfo is not None
        assert updated.start_time.utcoffset() == timedelta(hours=5)
        assert updated.timezone == KARACHI_TZ_NAME
        await session.commit()


# ==============================================================================
# T7: Reconnect Endpoint Returns 409 OAUTH_REAUTH_REQUIRED in Live Mode (A4)
# ==============================================================================
@pytest.mark.asyncio
async def test_t7_reconnect_requires_oauth_in_live_mode(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifies that when USE_FAKE_INTEGRATIONS=false, POST /connectors/{id}/reconnect
    returns HTTP 409 with code='OAUTH_REAUTH_REQUIRED' and a signed authorization_url,
    while fake mode continues restoring status='connected'.
    """
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.reconn_token",
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # Revoke first
    del_resp = await async_client.delete(f"/api/v1/connectors/{cred_id}", headers=headers)
    assert del_resp.status_code == 200

    # Live mode -> 409 OAUTH_REAUTH_REQUIRED
    monkeypatch.setattr(settings, "USE_FAKE_INTEGRATIONS", False)
    live_reconn = await async_client.post(
        f"/api/v1/connectors/{cred_id}/reconnect",
        headers=headers,
    )
    assert live_reconn.status_code == 409
    body = live_reconn.json()
    assert body["code"] == "OAUTH_REAUTH_REQUIRED"
    assert body["authorization_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")

    # Fake mode -> restores connected
    monkeypatch.setattr(settings, "USE_FAKE_INTEGRATIONS", True)
    fake_reconn = await async_client.post(
        f"/api/v1/connectors/{cred_id}/reconnect",
        headers=headers,
    )
    assert fake_reconn.status_code == 200
    assert fake_reconn.json()["status"] == "connected"


# ==============================================================================
# T8: Direct Temporal Activities & FakeEventDispatcher Fail Closed in Live Mode (B2)
# ==============================================================================
@pytest.mark.asyncio
async def test_t8_activities_and_fake_dispatcher_fail_closed_in_live_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifies that direct Stage 1/2 Temporal activities and FakeEventDispatcher refuse
    to execute raw integration clients when USE_FAKE_INTEGRATIONS=false.
    """
    monkeypatch.setattr(settings, "USE_FAKE_INTEGRATIONS", False)

    with pytest.raises(ApplicationError) as exc_info:
        await send_welcome_notification("Ali Khan", "ali@example.com")
    assert exc_info.value.non_retryable is True
    assert "execute_connector_call" in str(exc_info.value)

    dispatcher = FakeEventDispatcher()
    fake_event = OutboxEvent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        event_type="approval.created",
        event_payload={"approval_type": "leave_request"},
    )
    with pytest.raises(PermanentDeliveryError) as disp_exc:
        await dispatcher.dispatch(fake_event)
    assert "EmailDeliveryService" in str(disp_exc.value)
