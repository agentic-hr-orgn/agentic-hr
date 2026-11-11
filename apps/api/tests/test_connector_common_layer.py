"""
Automated Test Suite for Common Connector Layer (Stage 3 Prompt 1).
Verifies:
1. All 4 Fake Services implement typed ABC interfaces (Gmail, Sheets, Drive, Calendar).
2. Deterministic fault injection (429, 503, 401) on all fake clients.
3. Call wrapper client-side read-only switch blocks write operations (ReadOnlyConnectorError).
4. Call wrapper per-tenant sliding-window Redis rate limiting (HTTP 429).
5. Call wrapper exponential backoff with jitter on transient errors (429/503).
6. Call wrapper automated circuit-breaker trip on 401 / invalid_grant.
7. Call wrapper immutable audit logging on all external write operations.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.clerk import register_test_signing_key
from app.core.redis import get_redis_client
from app.db.session import AsyncSessionLocal
from app.integrations.base import (
    CalendarEvent,
    CalendarEventUpdate,
    DriveFile,
    EmailMessage,
)
from app.integrations.fake.fake_calendar import FakeCalendarService
from app.integrations.fake.fake_drive import FakeDriveService
from app.integrations.fake.fake_gmail import FakeGmailService
from app.integrations.fake.fake_sheets import FakeSheetsService
from app.models.audit_log import AuditLog
from app.models.connector import ConnectorCredential
from app.models.tenant import set_tenant_context
from app.services.connector_call_wrapper import (
    execute_connector_call,
    execute_connector_call_for_tenant,
)
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorRateLimitError,
    ConnectorService,
    ConnectorServiceUnavailableError,
    ReadOnlyConnectorError,
)
from tests.isolation_harness import (
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
)


@pytest.fixture(autouse=True)
def setup_isolation_keys():
    """Ensure isolation test signing keys are registered for offline JWT validation."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    yield


@pytest.fixture(autouse=True)
async def cleanup_test_state():
    """Flushes Redis rate limit keys and resets in-memory fakes before and after each test."""
    # Reset fakes
    FakeGmailService().reset()
    FakeSheetsService().reset()
    FakeDriveService().reset()
    FakeCalendarService().reset()

    # Flush Redis rate limit keys
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


# =============================================================================
# 1. Test All Fake Services Implement ABC Interfaces
# =============================================================================
@pytest.mark.asyncio
async def test_01_all_fake_services_implement_abc_interfaces() -> None:
    """Verifies that all 4 fake clients implement every method on their respective ABCs."""
    # 1. FakeGmailService
    email_svc = FakeGmailService()
    email_msg = EmailMessage(
        to_email="test.candidate@acmetech.internal",
        subject="Interview Invitation",
        body_text="Your interview is scheduled.",
        body_html="<p>Your interview is scheduled.</p>",
    )
    send_res = await email_svc.send_email(email_msg, access_token="fake_token")
    assert send_res["status"] == "success"
    assert send_res["fake"] is True
    assert len(email_svc.sent_messages) == 1

    # 2. FakeSheetsService
    sheets_svc = FakeSheetsService()
    sheet_id = await sheets_svc.create_or_find_spreadsheet("Test Sync Sheet")
    assert sheet_id is not None
    append_res = await sheets_svc.append_rows(
        sheet_id,
        "Employees!A:F",
        [["EMP-TEST-01", "Test Candidate", "test@acmetech.internal", "Eng", "Dev", "Active"]],
    )
    assert append_res["updates"]["updatedRows"] == 1
    rows = await sheets_svc.read_range(sheet_id, "Employees!A1:F10")
    assert len(rows) >= 2  # Header + appended row
    update_res = await sheets_svc.update_rows(
        sheet_id,
        "Employees!A2:F2",
        [["EMP-TEST-01", "Updated Name", "test@acmetech.internal", "Eng", "Senior Dev", "Active"]],
    )
    assert update_res is not None

    # 3. FakeDriveService
    drive_svc = FakeDriveService()
    folder_id = await drive_svc.ensure_tenant_folder("Tenant CVs")
    assert folder_id.startswith("fake_folder_")
    fake_cv = b"%PDF-1.4 Mock CV Content for DocuVerify OCR"
    uploaded = await drive_svc.upload_file(
        name="test_cv.pdf",
        content=fake_cv,
        mime_type="application/pdf",
        folder_id=folder_id,
    )
    assert isinstance(uploaded, DriveFile)
    assert uploaded.name == "test_cv.pdf"
    assert uploaded.size_bytes == len(fake_cv)
    assert uploaded.folder_id == folder_id

    downloaded = await drive_svc.download_file(uploaded.file_id)
    assert downloaded == fake_cv

    file_list = await drive_svc.list_files(folder_id=folder_id)
    assert len(file_list) == 1
    assert file_list[0].file_id == uploaded.file_id

    # 4. FakeCalendarService
    cal_svc = FakeCalendarService()
    now = datetime.now(UTC)
    start = now + timedelta(hours=1)
    end = start + timedelta(minutes=45)

    created_event = await cal_svc.create_event(
        CalendarEvent(
            title="Technical Screening",
            start_time=start,
            end_time=end,
            attendees=["interviewer@acme.com", "candidate@acme.com"],
        )
    )
    assert created_event["status"] == "confirmed"
    assert "meet.google.com" in created_event["hangout_link"]
    ev_id = created_event["event_id"]

    # Check free/busy & conflicts
    has_conflict = await cal_svc.check_conflicts(
        ["primary"], start_time=start + timedelta(minutes=10), end_time=end
    )
    assert has_conflict is True

    no_conflict = await cal_svc.check_conflicts(
        ["primary"], start_time=end + timedelta(hours=2), end_time=end + timedelta(hours=3)
    )
    assert no_conflict is False

    # Update event
    update_res = await cal_svc.update_event(
        ev_id, CalendarEventUpdate(title="Rescheduled Screening")
    )
    assert update_res["status"] == "updated"
    assert update_res["details"]["title"] == "Rescheduled Screening"

    # Cancel event
    cancelled = await cal_svc.cancel_event(ev_id)
    assert cancelled is True
    assert len(cal_svc.events) == 0


# =============================================================================
# 2. Test Deterministic Fault Injection on All Fake Services
# =============================================================================
@pytest.mark.asyncio
async def test_02_fake_fault_injection_429_503_401() -> None:
    """Verifies that each fake client raises simulated 429, 503, and 401 exceptions."""
    email_svc = FakeGmailService()
    sheets_svc = FakeSheetsService()
    drive_svc = FakeDriveService()
    cal_svc = FakeCalendarService()

    # 1. 429 Too Many Requests
    email_svc.simulate_429_on_next_call = True
    with pytest.raises(ConnectorRateLimitError) as exc_429:
        await email_svc.send_email(EmailMessage(to_email="a@b.com", subject="s", body_text="t"))
    assert exc_429.value.retry_after >= 1

    sheets_svc.simulate_429_on_next_call = True
    with pytest.raises(ConnectorRateLimitError):
        await sheets_svc.read_range("id", "range")

    drive_svc.simulate_429_on_next_call = True
    with pytest.raises(ConnectorRateLimitError):
        await drive_svc.list_files()

    cal_svc.simulate_429_on_next_call = True
    with pytest.raises(ConnectorRateLimitError):
        await cal_svc.create_event(
            CalendarEvent(
                title="t",
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC) + timedelta(hours=1),
                attendees=[],
            )
        )

    # 2. 503 Service Unavailable
    email_svc.simulate_503_on_next_call = True
    with pytest.raises(ConnectorServiceUnavailableError):
        await email_svc.send_email(EmailMessage(to_email="a@b.com", subject="s", body_text="t"))

    sheets_svc.simulate_503_on_next_call = True
    with pytest.raises(ConnectorServiceUnavailableError):
        await sheets_svc.append_rows("id", "range", [["val"]])

    drive_svc.simulate_503_on_next_call = True
    with pytest.raises(ConnectorServiceUnavailableError):
        await drive_svc.download_file("f_id")

    cal_svc.simulate_503_on_next_call = True
    with pytest.raises(ConnectorServiceUnavailableError):
        await cal_svc.cancel_event("ev_id")

    # 3. 401 Unauthorized / invalid_grant
    email_svc.simulate_401_on_next_call = True
    with pytest.raises(ConnectorAuthExpiredError):
        await email_svc.send_email(EmailMessage(to_email="a@b.com", subject="s", body_text="t"))

    sheets_svc.simulate_401_on_next_call = True
    with pytest.raises(ConnectorAuthExpiredError):
        await sheets_svc.create_or_find_spreadsheet("Title")

    drive_svc.simulate_401_on_next_call = True
    with pytest.raises(ConnectorAuthExpiredError):
        await drive_svc.upload_file("test.pdf", b"bytes", "application/pdf")

    cal_svc.simulate_401_on_next_call = True
    with pytest.raises(ConnectorAuthExpiredError):
        await cal_svc.get_free_busy(
            ["primary"], datetime.now(UTC), datetime.now(UTC) + timedelta(hours=1)
        )


# =============================================================================
# Helper Fixture to Create a Valid Connector Credential in DB
# =============================================================================
async def create_test_credential(
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    is_read_only: bool = False,
    status: str = "connected",
) -> ConnectorCredential:
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id, actor_id)
        cred = await ConnectorService.store_credentials(
            session=session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            provider="google",
            account_email=f"test.auth.{uuid.uuid4().hex[:6]}@gmail.com",
            scopes=["gmail.send", "drive.file", "calendar.events"],
            access_token="ya29.test_valid_access_token",
            refresh_token="1//test_valid_refresh_token",
        )
        if is_read_only:
            cred.is_read_only = True
        if status != "connected":
            cred.status = status
            if status == "needs_reconnect":
                cred.jobs_paused = True
        await session.flush()
        await session.commit()
        return cred


# =============================================================================
# 3. Test Call Wrapper Read-Only Mode Blocks Writes (HTTP 403)
# =============================================================================
@pytest.mark.asyncio
async def test_03_call_wrapper_read_only_blocks_writes(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that when is_read_only=True on the credential,
    mutating operations (is_write=True) immediately raise ReadOnlyConnectorError (HTTP 403)
    before any external API call is invoked. Read operations (is_write=False) succeed.
    """
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    cred = await create_test_credential(tenant_id, actor_id, is_read_only=True)

    call_executed = False

    async def sample_write_call(token: str) -> dict[str, str]:
        nonlocal call_executed
        call_executed = True
        return {"status": "written"}

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id, actor_id)
        # Attempt write operation -> must raise ReadOnlyConnectorError
        with pytest.raises(ReadOnlyConnectorError) as exc_info:
            await execute_connector_call(
                session=session,
                tenant_id=tenant_id,
                credential=cred,
                operation="email.send",
                is_write=True,
                call_factory=sample_write_call,
                actor_id=actor_id,
            )
        assert "read-only mode" in str(exc_info.value)
        assert call_executed is False

        # Attempt read operation -> must succeed
        async def sample_read_call(token: str) -> dict[str, str]:
            return {"status": "read_success"}

        read_res = await execute_connector_call(
            session=session,
            tenant_id=tenant_id,
            credential=cred,
            operation="sheets.read",
            is_write=False,
            call_factory=sample_read_call,
            actor_id=actor_id,
        )
        assert read_res["status"] == "read_success"


# =============================================================================
# 4. Test Call Wrapper Per-Tenant Sliding-Window Rate Limiting
# =============================================================================
@pytest.mark.asyncio
async def test_04_call_wrapper_rate_limiting_enforcement(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that exceeding the per-tenant rate limit returns HTTP 429 with Retry-After."""
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    cred = await create_test_credential(tenant_id, actor_id, is_read_only=False)

    async def mock_call(token: str) -> str:
        return "ok"

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id, actor_id)
        # Call 3 times with max_requests_per_minute=2
        res1 = await execute_connector_call(
            session=session,
            tenant_id=tenant_id,
            credential=cred,
            operation="test.rl",
            is_write=False,
            call_factory=mock_call,
            max_requests_per_minute=2,
            rate_limit_scope="test_scope_strict",
        )
        assert res1 == "ok"

        res2 = await execute_connector_call(
            session=session,
            tenant_id=tenant_id,
            credential=cred,
            operation="test.rl",
            is_write=False,
            call_factory=mock_call,
            max_requests_per_minute=2,
            rate_limit_scope="test_scope_strict",
        )
        assert res2 == "ok"

        # 3rd request must raise HTTP 429
        with pytest.raises(HTTPException) as exc_429:
            await execute_connector_call(
                session=session,
                tenant_id=tenant_id,
                credential=cred,
                operation="test.rl",
                is_write=False,
                call_factory=mock_call,
                max_requests_per_minute=2,
                rate_limit_scope="test_scope_strict",
            )
        assert exc_429.value.status_code == 429
        assert "Retry-After" in exc_429.value.headers


# =============================================================================
# 5. Test Exponential Backoff with Jitter on Transient Errors (429/503)
# =============================================================================
@pytest.mark.asyncio
async def test_05_call_wrapper_exponential_backoff_on_transient_errors(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that transient 503 or 429 errors trigger bounded retries with backoff."""
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    cred = await create_test_credential(tenant_id, actor_id, is_read_only=False)

    call_count = 0

    async def transient_failing_call(token: str) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            # First 2 attempts fail with transient 503
            raise ConnectorServiceUnavailableError("Simulated 503 Service Unavailable")
        return {"status": "recovered_on_attempt_3"}

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id, actor_id)
        result = await execute_connector_call(
            session=session,
            tenant_id=tenant_id,
            credential=cred,
            operation="drive.upload",
            is_write=True,
            call_factory=transient_failing_call,
            max_retries=3,
            base_backoff_seconds=0.02,  # Fast backoff for unit tests
            actor_id=actor_id,
        )
        assert result["status"] == "recovered_on_attempt_3"
        assert call_count == 3


# =============================================================================
# 6. Test Automated Circuit Breaker Trip on 401 / invalid_grant
# =============================================================================
@pytest.mark.asyncio
async def test_06_call_wrapper_auth_failure_circuit_breaker(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that on 401 / invalid_grant:
    - No retries are made.
    - Credential status immediately transitions to 'needs_reconnect'.
    - jobs_paused is set to True.
    - Subsequent calls are blocked by the circuit breaker.
    """
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    cred = await create_test_credential(tenant_id, actor_id, is_read_only=False)

    attempt_count = 0

    async def auth_failing_call(token: str) -> None:
        nonlocal attempt_count
        attempt_count += 1
        raise ConnectorAuthExpiredError("Simulated 401 OAuth token revoked or expired")

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id, actor_id)
        with pytest.raises(ConnectorAuthExpiredError):
            await execute_connector_call(
                session=session,
                tenant_id=tenant_id,
                credential=cred,
                operation="sheets.sync",
                is_write=True,
                call_factory=auth_failing_call,
                max_retries=3,
                actor_id=actor_id,
            )

        # Proves no retries were performed
        assert attempt_count == 1

        # Check DB status
        updated_cred = await ConnectorService.get_credential_by_id(session, tenant_id, cred.id)
        assert updated_cred.status == "needs_reconnect"
        assert updated_cred.jobs_paused is True
        assert "401" in (updated_cred.error_message or "")

        # Subsequent call must be immediately rejected by the circuit breaker guard
        with pytest.raises(ConnectorAuthExpiredError) as blocked_info:
            await execute_connector_call(
                session=session,
                tenant_id=tenant_id,
                credential=updated_cred,
                operation="sheets.sync",
                is_write=True,
                call_factory=auth_failing_call,
                actor_id=actor_id,
            )
        assert "requires reconnect" in str(blocked_info.value)


# =============================================================================
# 7. Test Immutable Audit Logging on Write Operations
# =============================================================================
@pytest.mark.asyncio
async def test_07_call_wrapper_audit_logging_on_writes(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that write operations (is_write=True) generate an immutable audit_logs
    entry in PostgreSQL, while read operations (is_write=False) do not generate audit noise.
    """
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    cred = await create_test_credential(tenant_id, actor_id, is_read_only=False)

    async def mock_write(token: str) -> dict[str, str]:
        return {"result": "saved"}

    async def mock_read(token: str) -> dict[str, str]:
        return {"result": "retrieved"}

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id, actor_id)

        # 1. Execute Write Operation via execute_connector_call_for_tenant
        await execute_connector_call_for_tenant(
            session=session,
            tenant_id=tenant_id,
            provider="google",
            operation="calendar.create_event",
            is_write=True,
            call_factory=mock_write,
            actor_id=actor_id,
            audit_details={"title": "Tech Screen Candidate A"},
        )
        await session.commit()

    # Verify audit_log entry exists in PostgreSQL
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id, actor_id)
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.action == "connector.calendar.create_event",
            )
            .order_by(AuditLog.timestamp.desc())
        )
        res = await session.execute(stmt)
        log_entry = res.scalars().first()
        assert log_entry is not None
        assert log_entry.target_type == "connector_credential"
        assert log_entry.target_id == str(cred.id)
        assert log_entry.details.get("title") == "Tech Screen Candidate A"
        assert log_entry.details.get("provider") == "google"

        # 2. Execute Read Operation
        await execute_connector_call(
            session=session,
            tenant_id=tenant_id,
            credential=cred,
            operation="calendar.get_free_busy",
            is_write=False,
            call_factory=mock_read,
            actor_id=actor_id,
        )
        await session.commit()

    # Verify that NO audit log was generated for the read operation
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id, actor_id)
        stmt_read = select(AuditLog).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.action == "connector.calendar.get_free_busy",
        )
        read_log_entry = (await session.execute(stmt_read)).scalars().first()
        assert read_log_entry is None
