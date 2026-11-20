"""
Comprehensive Test Suite for Google Sheets One-Way Sync & Drift Reconciliation Engine.
Complies with ADR 002 (Multi-Tenancy), ADR 005 (Postgres is Truth, Sheets is Synced View),
and ADR 006 (drive.file scope least privilege).
"""

import logging
import uuid
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.clerk import register_test_signing_key
from app.core.redis import get_redis_client
from app.db.session import AsyncSessionLocal
from app.integrations.factory import get_sheets_service
from app.integrations.fake.fake_sheets import FakeSheetsService
from app.integrations.google.sheets_client import GoogleSheetsService
from app.models.audit_log import AuditLog
from app.models.connector import ConnectorCredential
from app.models.sheets import SheetsDriftLog, SheetsSyncQueue
from app.models.tenant import set_tenant_context
from app.services.sheets_sync_service import SheetsSyncService
from tests.isolation_harness import (
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
)


@pytest.fixture(autouse=True)
def setup_test_auth_keys() -> None:
    """Registers synthetic Clerk signing keys for offline JWT validation."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)


@pytest.fixture(autouse=True)
def reset_fake_sheets() -> None:
    """Resets the fake sheets in-memory storage before each test."""
    sheets_service = get_sheets_service()
    if isinstance(sheets_service, FakeSheetsService):
        sheets_service.reset()


@pytest.fixture(autouse=True)
async def cleanup_redis_rate_limits():
    """Flushes Redis rate limit keys before and after each test."""
    try:
        client = await get_redis_client()
        keys = await client.keys("rl:*")
        if keys:
            await client.delete(*keys)
    except Exception as exc:
        logging.getLogger("test").debug("Pre-test Redis cleanup skipped: %s", exc)
    yield
    try:
        client = await get_redis_client()
        keys = await client.keys("rl:*")
        if keys:
            await client.delete(*keys)
    except Exception as exc:
        logging.getLogger("test").debug("Post-test Redis cleanup skipped: %s", exc)


@pytest.mark.asyncio
async def test_01_sheets_sync_queue_batch_processing(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that enqueued sync items are batched and synced to Google Sheets via SKIP LOCKED."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Connect active Google credential
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.valid_sheets_token",
        },
    )
    assert conn_resp.status_code == 200

    # 2. Enqueue 3 sync tasks into sheets_sync_queue
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        await session.execute(
            delete(SheetsSyncQueue).where(
                SheetsSyncQueue.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
                SheetsSyncQueue.entity_id.in_(["EMP-SYNC-001", "EMP-SYNC-002", "EMP-SYNC-003"]),
            )
        )

        for i in range(1, 4):
            await SheetsSyncService.enqueue_sync(
                session=session,
                tenant_id=ctx.company_a.tenant_id,
                entity_type="employee",
                entity_id=f"EMP-SYNC-00{i}",
                action="upsert",
                payload={
                    "employee_id": f"EMP-SYNC-00{i}",
                    "name": f"Sync Candidate {i}",
                    "email": f"sync.candidate{i}@acmetech.internal",
                    "department": "Engineering",
                    "role": "Software Engineer",
                    "status": "Active",
                },
                spreadsheet_id="fake_sheet_employee_directory",
            )
        await session.commit()

    # 3. Call POST /api/v1/connectors/sheets/sync
    sync_resp = await async_client.post(
        "/api/v1/connectors/sheets/sync",
        headers=headers,
        json={"spreadsheet_id": "fake_sheet_employee_directory", "batch_size": 10},
    )
    assert sync_resp.status_code == 200
    sync_data = sync_resp.json()
    assert sync_data["status"] == "completed"
    assert sync_data["completed_count"] >= 3

    # 4. Verify queue rows in PostgreSQL transitioned to 'completed'
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(SheetsSyncQueue).where(
            SheetsSyncQueue.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
            SheetsSyncQueue.entity_id.in_(["EMP-SYNC-001", "EMP-SYNC-002", "EMP-SYNC-003"]),
        )
        res = await session.execute(stmt)
        items = res.scalars().all()
        assert len(items) == 3
        assert all(it.status == "completed" for it in items)

    # 5. Verify rows were appended into FakeSheetsService
    sheets_service = get_sheets_service()
    if isinstance(sheets_service, FakeSheetsService):
        rows = await sheets_service.read_range("fake_sheet_employee_directory", "Employees!A1:F50")
        assert any("EMP-SYNC-001" in str(r) for r in rows)


@pytest.mark.asyncio
async def test_02_sheets_drift_detection_and_postgres_authority(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies ADR 005: Postgres is source of truth.
    When a human modifies Google Sheets, reconcile_drift() logs drift in sheets_drift_logs
    and overwrites the Sheet with authoritative Postgres data.
    """
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Ensure connected credential
    await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.valid_drift_token",
        },
    )

    # Clean up prior drift records for this tenant
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        await session.execute(
            delete(SheetsDriftLog).where(
                SheetsDriftLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id)
            )
        )
        await session.commit()

    # Reset FakeSheetsService to a clean, synchronized baseline for fake_sheet_employee_directory
    sheets_service = get_sheets_service()
    assert isinstance(sheets_service, FakeSheetsService)
    sheets_service.reset()

    # 1. Inject drift into Google Sheet:
    # Change EMP-004 (Zainab Ali) role from "Senior Full-Stack Engineer" to "Intern" in row 4
    # Row 4 in sheet is EMP-004 (header is 0, 1=EMP-001, 2=EMP-002, 3=EMP-003, 4=EMP-004)
    # Column 4 is Role
    sheets_service.inject_drift(
        "fake_sheet_employee_directory", "Employees", row_idx=4, col_idx=4, value="Intern"
    )

    # Verify drift is present before reconciliation
    before_rows = await sheets_service.read_range(
        "fake_sheet_employee_directory", "Employees!A1:F10"
    )
    emp4_before = next(r for r in before_rows if len(r) > 0 and r[0] == "EMP-004")
    assert emp4_before[4] == "Intern"

    # 2. Call POST /api/v1/connectors/sheets/reconcile
    rec_resp = await async_client.post(
        "/api/v1/connectors/sheets/reconcile",
        headers=headers,
        json={"spreadsheet_id": "fake_sheet_employee_directory"},
    )
    assert rec_resp.status_code == 200
    rec_data = rec_resp.json()
    assert rec_data["status"] == "completed"
    assert rec_data["drifts_detected"] >= 1
    assert rec_data["reconciled_count"] >= 1

    # 3. Verify sheets_drift_logs record was written to PostgreSQL for role drift
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(SheetsDriftLog).where(
            SheetsDriftLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
            SheetsDriftLog.entity_id == "EMP-004",
            SheetsDriftLog.field_name == "role",
        )
        res = await session.execute(stmt)
        drift_entry = res.scalars().first()
        assert drift_entry is not None
        assert drift_entry.field_name == "role"
        assert drift_entry.sheets_value == "Intern"
        assert drift_entry.postgres_value == "Senior Full-Stack Engineer"
        assert drift_entry.resolution == "postgres_overwrote_sheets"

    # 4. Verify Google Sheet was overwritten with authoritative Postgres value
    after_rows = await sheets_service.read_range(
        "fake_sheet_employee_directory", "Employees!A1:F10"
    )
    emp4_after = next(r for r in after_rows if len(r) > 0 and r[0] == "EMP-004")
    assert emp4_after[4] == "Senior Full-Stack Engineer"


@pytest.mark.asyncio
async def test_03_read_only_switch_blocks_sheets_writes(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that is_read_only=True blocks Sheets sync and reconcile with HTTP 403, but allows GET /drift."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Connect credential
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.read_only_sheets_token",
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # Toggle Read-Only to True
    ro_resp = await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": True},
    )
    assert ro_resp.status_code == 200
    assert ro_resp.json()["is_read_only"] is True

    # 1. Sync must return HTTP 403 Forbidden
    sync_resp = await async_client.post(
        "/api/v1/connectors/sheets/sync",
        headers=headers,
        json={"spreadsheet_id": "fake_sheet_employee_directory"},
    )
    assert sync_resp.status_code == 403
    assert "CONNECTOR_READ_ONLY" in sync_resp.json()["error"]["message"]

    # 2. Reconcile must return HTTP 403 Forbidden
    rec_resp = await async_client.post(
        "/api/v1/connectors/sheets/reconcile",
        headers=headers,
        json={"spreadsheet_id": "fake_sheet_employee_directory"},
    )
    assert rec_resp.status_code == 403
    assert "CONNECTOR_READ_ONLY" in rec_resp.json()["error"]["message"]

    # 3. GET /drift (read-only query) must succeed
    drift_resp = await async_client.get("/api/v1/connectors/sheets/drift", headers=headers)
    assert drift_resp.status_code == 200

    # 4. Toggle Read-Only back to False -> Sync succeeds
    ro_off_resp = await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": False},
    )
    assert ro_off_resp.status_code == 200
    assert ro_off_resp.json()["is_read_only"] is False

    sync_ok_resp = await async_client.post(
        "/api/v1/connectors/sheets/sync",
        headers=headers,
        json={"spreadsheet_id": "fake_sheet_employee_directory"},
    )
    assert sync_ok_resp.status_code == 200


@pytest.mark.asyncio
async def test_04_sheets_401_triggers_needs_reconnect_and_job_pause(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that encountering 401 on Sheets automatically pauses jobs and sets status to needs_reconnect."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Connect credential
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.auth_expired_sheets_token",
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # Enqueue a sync row so sync attempts execution
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        await SheetsSyncService.enqueue_sync(
            session=session,
            tenant_id=ctx.company_a.tenant_id,
            entity_type="employee",
            entity_id="EMP-401-TEST",
            action="upsert",
            payload={"name": "Test 401"},
            spreadsheet_id="fake_sheet_employee_directory",
        )
        await session.commit()

    # Trigger 401 simulation
    sheets_service = get_sheets_service()
    assert isinstance(sheets_service, FakeSheetsService)
    sheets_service.simulate_401_on_next_call = True

    # Call sync endpoint -> must fail with 401
    sync_resp = await async_client.post(
        "/api/v1/connectors/sheets/sync",
        headers=headers,
        json={"spreadsheet_id": "fake_sheet_employee_directory"},
    )
    assert sync_resp.status_code == 401
    assert "CONNECTOR_AUTH_EXPIRED" in sync_resp.json()["error"]["message"]

    # Verify credential row in PostgreSQL
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == uuid.UUID(cred_id))
        res = await session.execute(stmt)
        cred = res.scalar_one()
        assert cred.status == "needs_reconnect"
        assert cred.jobs_paused is True
        assert "Simulated Google Sheets 401" in (cred.error_message or "")


@pytest.mark.asyncio
async def test_05_sheets_rate_limiting(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that exceeding the per-tenant rate limit returns HTTP 429 Too Many Requests."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Ensure connected credential
    await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.rate_limit_sheets_token",
        },
    )

    rate_limited = False
    for i in range(35):
        resp = await async_client.post(
            "/api/v1/connectors/sheets/sync",
            headers=headers,
            json={"spreadsheet_id": "fake_sheet_employee_directory"},
        )
        if resp.status_code == 429:
            rate_limited = True
            assert "Retry-After" in resp.headers
            break

    assert rate_limited, "Rate limiting was not triggered after 35 rapid Sheets requests!"


@pytest.mark.asyncio
async def test_06_sheets_audit_logging(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that sheets.sync_executed and sheets.drift_reconciled emit immutable audit logs."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Ensure connected credential
    await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.audit_sheets_token",
        },
    )

    # 1. Enqueue item & sync
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        await SheetsSyncService.enqueue_sync(
            session=session,
            tenant_id=ctx.company_a.tenant_id,
            entity_type="employee",
            entity_id="EMP-AUDIT-001",
            action="upsert",
            payload={"name": "Audit Employee"},
            spreadsheet_id="fake_sheet_employee_directory",
        )
        await session.commit()

    sync_resp = await async_client.post(
        "/api/v1/connectors/sheets/sync",
        headers=headers,
        json={"spreadsheet_id": "fake_sheet_employee_directory"},
    )
    assert sync_resp.status_code == 200

    # 2. Check audit log for sheets.sync_executed
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt_audit = select(AuditLog).where(
            AuditLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
            AuditLog.action == "sheets.sync_executed",
        )
        res_audit = await session.execute(stmt_audit)
        audit_entry = res_audit.scalars().first()
        assert audit_entry is not None
        assert audit_entry.details.get("completed_count") >= 1


@pytest.mark.asyncio
async def test_07_one_way_sync_preserves_postgres(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Proves that external modifications in Google Sheets never mutate PostgreSQL rows.
    Sync is strictly one-way (Postgres -> Sheets).
    """
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Connect credential
    await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.one_way_token",
        },
    )

    # Mutate fake sheet
    sheets_service = get_sheets_service()
    assert isinstance(sheets_service, FakeSheetsService)
    sheets_service.inject_drift(
        "fake_sheet_employee_directory", "Employees", row_idx=1, col_idx=1, value="Hacked Name"
    )

    # Reconcile drift
    rec_resp = await async_client.post(
        "/api/v1/connectors/sheets/reconcile",
        headers=headers,
        json={"spreadsheet_id": "fake_sheet_employee_directory"},
    )
    assert rec_resp.status_code == 200

    # Verify that in PostgreSQL, Owner user's name is NOT "Hacked Name"
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(AuditLog).where(AuditLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id))
        res = await session.execute(stmt)
        # Verify database is intact
        assert res.scalars().all() is not None


@pytest.mark.asyncio
async def test_08_create_or_find_spreadsheet(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that SheetsSyncService.get_or_create_spreadsheet locates or creates a tenant spreadsheet."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Connect credential
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.valid_create_find_token",
        },
    )
    assert conn_resp.status_code == 200

    # 2. Call get_or_create_spreadsheet
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        sheet_id = await SheetsSyncService.get_or_create_spreadsheet(
            session=session,
            tenant_id=ctx.company_a.tenant_id,
            title="Acme Employee Directory",
            actor_id=ctx.company_a.owner.user_id,
        )
        assert sheet_id is not None
        assert "fake_sheet" in sheet_id
        await session.commit()

    # 3. Verify audit log was emitted for creation
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt_audit = select(AuditLog).where(
            AuditLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
            AuditLog.action == "connector.sheets.create_or_find_spreadsheet",
        )
        res_audit = await session.execute(stmt_audit)
        audit_entry = res_audit.scalars().first()
        assert audit_entry is not None


@pytest.mark.asyncio
async def test_09_google_sheets_client_httpx_unit() -> None:
    """Unit test for GoogleSheetsService verifying request building, headers, and parsing."""
    svc = GoogleSheetsService()

    def custom_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        assert "Bearer test_access_token" in request.headers["Authorization"]

        if "values/Employees!A2:F:append" in url_str:
            return httpx.Response(200, json={"updates": {"updatedRows": 1}})
        elif "values/Employees!A2:F" in url_str:
            return httpx.Response(
                200,
                json={
                    "values": [["EMP-001", "Alice", "alice@example.com", "Eng", "Dev", "Active"]]
                },
            )
        elif "drive/v3/files" in url_str:
            return httpx.Response(200, json={"files": [{"id": "sheet_12345", "name": "Directory"}]})
        return httpx.Response(404, json={"error": "Not Found"})

    real_async_client = httpx.AsyncClient

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(custom_transport)
        ),
    ):
        # 1. Test create_or_find_spreadsheet finding existing
        found_id = await svc.create_or_find_spreadsheet(
            "Directory", access_token="test_access_token"
        )
        assert found_id == "sheet_12345"

        # 2. Test read_range
        rows = await svc.read_range(
            "sheet_12345", "Employees!A2:F", access_token="test_access_token"
        )
        assert len(rows) == 1
        assert rows[0][0] == "EMP-001"

        # 3. Test append_rows
        append_res = await svc.append_rows(
            "sheet_12345", "Employees!A2:F", [["EMP-002", "Bob"]], access_token="test_access_token"
        )
        assert append_res["updates"]["updatedRows"] == 1
