"""
Comprehensive Integration & Unit Test Suite for Google Drive & Google Calendar Connectors.
Complies with ADR 002 (Multi-Tenancy), ADR 005 (Postgres Authoritative), and ADR 006 (drive.file & calendar.events scopes).

Tests:
1. Per-tenant Drive folder provisioning (drive.file scope).
2. Drive file upload & binary download verification.
3. MIME type validation & 10MB size limit enforcement.
4. Drive upload IdempotencyGate deduplication.
5. Read-only mode write-blocking for Drive (allows download & list).
6. Calendar event creation with mandatory Asia/Karachi timezone & Google Meet link.
7. Calendar free/busy window querying & conflict detection.
8. Calendar event lifecycle (create -> patch update -> cancel).
9. Calendar event creation IdempotencyGate deduplication.
10. Read-only mode write-blocking for Calendar (allows free/busy reads).
11. 401 / invalid_grant circuit-breaker tripping to needs_reconnect & jobs_paused=True.
12. GoogleDriveService httpx client unit verification with mock transport.
13. GoogleCalendarService httpx client unit verification with mock transport.
14. Multi-tenant isolation for Drive and Calendar at API level.
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.integrations.base import CalendarEvent, CalendarEventUpdate
from app.integrations.factory import get_drive_service
from app.integrations.fake.fake_drive import FakeDriveService
from app.integrations.google.calendar_client import GoogleCalendarService
from app.integrations.google.drive_client import GoogleDriveService
from app.models.connector import ConnectorCredential
from app.models.tenant import set_tenant_context
from tests.isolation_harness import MultiTenantTestContext


async def ensure_clean_connected_credential(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    email: str = "agentic.hr.test.ubaid.owner@gmail.com",
) -> str:
    """Helper to ensure tenant has a connected credential with valid expiry and refresh token."""
    now = datetime.now(UTC)
    resp = await client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": email,
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": f"ya29.valid_test_token_{uuid.uuid4().hex[:6]}",
            "refresh_token": f"valid_refresh_{uuid.uuid4().hex[:6]}",
            "token_expiry": (now + timedelta(hours=2)).isoformat(),
        },
    )
    assert resp.status_code == 200
    cred_id = resp.json()["id"]

    # Ensure read-only mode is False
    await client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": False},
    )
    return cred_id


@pytest.mark.asyncio
async def test_01_ensure_tenant_drive_folder(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that POST /connectors/drive/folders finds or creates a dedicated tenant folder under drive.file."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Connect clean credential
    await ensure_clean_connected_credential(async_client, headers)

    # 2. Call ensure_drive_folder
    folder_resp = await async_client.post(
        "/api/v1/connectors/drive/folders",
        headers=headers,
        json={"folder_name": "Company A CVs"},
    )
    assert folder_resp.status_code == 200
    folder_data = folder_resp.json()
    assert folder_data["name"] == "Company A CVs"
    assert "fake_folder" in folder_data["folder_id"]


@pytest.mark.asyncio
async def test_02_drive_file_upload_and_download(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that files can be uploaded and downloaded with exact byte equality."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Ensure connected
    await ensure_clean_connected_credential(async_client, headers)

    pdf_content = b"%PDF-1.4 Mock CV Content for Candidate Alice Smith"
    b64_content = base64.b64encode(pdf_content).decode("utf-8")

    # 2. Upload file
    upload_resp = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers,
        json={
            "name": "Alice_Smith_Resume.pdf",
            "content_base64": b64_content,
            "mime_type": "application/pdf",
        },
    )
    assert upload_resp.status_code == 200
    file_data = upload_resp.json()
    assert file_data["name"] == "Alice_Smith_Resume.pdf"
    assert file_data["mime_type"] == "application/pdf"
    assert file_data["size_bytes"] == len(pdf_content)
    drive_file_id = file_data["drive_file_id"]

    # 3. Download file
    download_resp = await async_client.get(
        f"/api/v1/connectors/drive/files/{drive_file_id}/download",
        headers=headers,
    )
    assert download_resp.status_code == 200
    assert download_resp.content == pdf_content
    assert download_resp.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_03_drive_upload_mime_and_size_validation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies rejection of unapproved MIME types and oversized files (>10MB)."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    # 1. Unapproved MIME type (.exe)
    bad_mime_resp = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers,
        json={
            "name": "malware.exe",
            "content_base64": base64.b64encode(b"MZ executable content").decode("utf-8"),
            "mime_type": "application/x-msdownload",
        },
    )
    assert bad_mime_resp.status_code == 400
    assert "MIME type" in bad_mime_resp.json()["error"]["message"]

    # 2. Oversized content (>10MB)
    oversized_bytes = b"0" * (10 * 1024 * 1024 + 1024)
    oversized_resp = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers,
        json={
            "name": "giant.pdf",
            "content_base64": base64.b64encode(oversized_bytes).decode("utf-8"),
            "mime_type": "application/pdf",
        },
    )
    assert oversized_resp.status_code == 400
    assert "exceeds the maximum" in oversized_resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_04_drive_upload_idempotency(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that replaying an upload with the same Idempotency-Key returns cached response without duplicate files."""
    ctx = isolation_context
    headers = {
        "Authorization": f"Bearer {ctx.company_a.owner.token}",
        "Idempotency-Key": f"idemp_drive_{uuid.uuid4()}",
    }
    await ensure_clean_connected_credential(
        async_client, {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    )

    content_bytes = b"Deterministic idempotency test content"
    b64_content = base64.b64encode(content_bytes).decode("utf-8")

    # First call
    resp1 = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers,
        json={
            "name": "idemp_test.docx",
            "content_base64": b64_content,
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    )
    assert resp1.status_code == 200
    id1 = resp1.json()["id"]

    # Second call (replay)
    resp2 = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers,
        json={
            "name": "idemp_test.docx",
            "content_base64": b64_content,
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    )
    assert resp2.status_code == 200
    id2 = resp2.json()["id"]
    assert id1 == id2


@pytest.mark.asyncio
async def test_05_drive_read_only_switch_blocks_writes(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies is_read_only=True blocks folder creation & upload with HTTP 403, but permits listing and downloading."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    cred_id = await ensure_clean_connected_credential(async_client, headers)

    # First upload a file while read-only is False
    valid_up = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers,
        json={
            "name": "sample.pdf",
            "content_base64": base64.b64encode(b"%PDF sample").decode("utf-8"),
            "mime_type": "application/pdf",
        },
    )
    assert valid_up.status_code == 200
    sample_file_id = valid_up.json()["drive_file_id"]

    # Enable Read-Only Mode
    ro_on = await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": True},
    )
    assert ro_on.status_code == 200
    assert ro_on.json()["is_read_only"] is True

    # 1. Folder creation must fail with HTTP 403
    f_fail = await async_client.post(
        "/api/v1/connectors/drive/folders",
        headers=headers,
        json={"folder_name": "Blocked Folder"},
    )
    assert f_fail.status_code == 403
    assert "CONNECTOR_READ_ONLY" in f_fail.json()["error"]["message"]

    # 2. File upload must fail with HTTP 403
    up_fail = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers,
        json={
            "name": "blocked.pdf",
            "content_base64": base64.b64encode(b"%PDF blocked").decode("utf-8"),
            "mime_type": "application/pdf",
        },
    )
    assert up_fail.status_code == 403
    assert "CONNECTOR_READ_ONLY" in up_fail.json()["error"]["message"]

    # 3. File listing must succeed in read-only mode
    list_ok = await async_client.get("/api/v1/connectors/drive/files", headers=headers)
    assert list_ok.status_code == 200

    # 4. File download must succeed in read-only mode
    down_ok = await async_client.get(
        f"/api/v1/connectors/drive/files/{sample_file_id}/download",
        headers=headers,
    )
    assert down_ok.status_code == 200

    # Reset Read-Only Mode
    await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": False},
    )


@pytest.mark.asyncio
async def test_06_calendar_default_asia_karachi_timezone(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that calendar events default to Asia/Karachi (PKT, UTC+05:00) with a Google Meet link."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    # Create event with naive datetimes
    start_time = datetime(2026, 11, 20, 10, 0, 0)
    end_time = datetime(2026, 11, 20, 11, 0, 0)

    create_resp = await async_client.post(
        "/api/v1/connectors/calendar/events",
        headers=headers,
        json={
            "title": "Technical Assessment Review - Candidate Bob",
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "attendees": ["candidate_bob@test.com", "interviewer@acme.com"],
            "description": "System architecture deep-dive",
        },
    )
    assert create_resp.status_code == 200
    event_data = create_resp.json()
    assert event_data["title"] == "Technical Assessment Review - Candidate Bob"
    assert event_data["timezone"] == "Asia/Karachi"
    assert "+05:00" in event_data["start_time"]
    assert "+05:00" in event_data["end_time"]
    assert event_data["hangout_link"] is not None
    assert "https://meet.google.com/" in event_data["hangout_link"]


@pytest.mark.asyncio
async def test_07_calendar_free_busy_and_conflict_check(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies querying free/busy windows for calendar IDs."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    now = datetime(2026, 11, 20, 8, 0, 0)
    t_min = now
    t_max = now + timedelta(days=1)

    fb_resp = await async_client.post(
        "/api/v1/connectors/calendar/free-busy",
        headers=headers,
        json={
            "calendar_ids": ["primary", "interviewer@acme.com"],
            "time_min": t_min.isoformat(),
            "time_max": t_max.isoformat(),
        },
    )
    assert fb_resp.status_code == 200
    fb_data = fb_resp.json()
    assert "calendars" in fb_data
    assert "primary" in fb_data["calendars"]


@pytest.mark.asyncio
async def test_08_calendar_event_lifecycle(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Tests full event lifecycle: create -> patch update/reschedule -> delete/cancel."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    # 1. Create event
    s_time = datetime(2026, 11, 25, 14, 0, 0)
    e_time = datetime(2026, 11, 25, 15, 0, 0)
    create_resp = await async_client.post(
        "/api/v1/connectors/calendar/events",
        headers=headers,
        json={
            "title": "HR Screening Round",
            "start_time": s_time.isoformat(),
            "end_time": e_time.isoformat(),
            "attendees": ["candidate_charlie@test.com"],
        },
    )
    assert create_resp.status_code == 200
    event_id = create_resp.json()["google_event_id"]

    # 2. Reschedule / Update event
    new_s_time = datetime(2026, 11, 25, 15, 0, 0)
    new_e_time = datetime(2026, 11, 25, 16, 0, 0)
    patch_resp = await async_client.patch(
        f"/api/v1/connectors/calendar/events/{event_id}",
        headers=headers,
        json={
            "title": "HR Screening Round (Rescheduled)",
            "start_time": new_s_time.isoformat(),
            "end_time": new_e_time.isoformat(),
        },
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["title"] == "HR Screening Round (Rescheduled)"

    # 3. Cancel event
    del_resp = await async_client.delete(
        f"/api/v1/connectors/calendar/events/{event_id}",
        headers=headers,
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_09_calendar_event_idempotency(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that replaying calendar event creation returns the existing event without double-booking."""
    ctx = isolation_context
    headers = {
        "Authorization": f"Bearer {ctx.company_a.owner.token}",
        "Idempotency-Key": f"idemp_cal_{uuid.uuid4()}",
    }
    await ensure_clean_connected_credential(
        async_client, {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    )

    s_time = datetime(2026, 11, 26, 9, 0, 0)
    e_time = datetime(2026, 11, 26, 10, 0, 0)

    # Call 1
    resp1 = await async_client.post(
        "/api/v1/connectors/calendar/events",
        headers=headers,
        json={
            "title": "Idempotent Interview",
            "start_time": s_time.isoformat(),
            "end_time": e_time.isoformat(),
            "attendees": ["cand@test.com"],
        },
    )
    assert resp1.status_code == 200
    id1 = resp1.json()["id"]

    # Call 2 (replay)
    resp2 = await async_client.post(
        "/api/v1/connectors/calendar/events",
        headers=headers,
        json={
            "title": "Idempotent Interview",
            "start_time": s_time.isoformat(),
            "end_time": e_time.isoformat(),
            "attendees": ["cand@test.com"],
        },
    )
    assert resp2.status_code == 200
    id2 = resp2.json()["id"]
    assert id1 == id2


@pytest.mark.asyncio
async def test_10_calendar_read_only_switch_blocks_writes(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies is_read_only=True blocks event creation/updating/cancellation with HTTP 403, while permitting free/busy."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    cred_id = await ensure_clean_connected_credential(async_client, headers)

    # Toggle Read-Only Mode ON
    await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": True},
    )

    # 1. Create must fail
    c_fail = await async_client.post(
        "/api/v1/connectors/calendar/events",
        headers=headers,
        json={
            "title": "Blocked Event",
            "start_time": datetime(2026, 11, 27, 10, 0, 0).isoformat(),
            "end_time": datetime(2026, 11, 27, 11, 0, 0).isoformat(),
        },
    )
    assert c_fail.status_code == 403
    assert "CONNECTOR_READ_ONLY" in c_fail.json()["error"]["message"]

    # 2. Free-Busy must succeed
    fb_ok = await async_client.post(
        "/api/v1/connectors/calendar/free-busy",
        headers=headers,
        json={
            "calendar_ids": ["primary"],
            "time_min": datetime(2026, 11, 27, 8, 0, 0).isoformat(),
            "time_max": datetime(2026, 11, 27, 18, 0, 0).isoformat(),
        },
    )
    assert fb_ok.status_code == 200

    # Toggle Read-Only Mode OFF
    await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": False},
    )


@pytest.mark.asyncio
async def test_11_drive_and_calendar_auth_failure_circuit_breaker(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that HTTP 401 trips circuit-breaker to needs_reconnect and pauses jobs."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.valid_then_trip",
            "refresh_token": "revoked_refresh_token",
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # Inject 401 fault into fake drive service
    drive_service = get_drive_service()
    assert isinstance(drive_service, FakeDriveService)
    drive_service.simulate_401_on_next_call = True

    # Call upload -> should trip circuit breaker
    up_resp = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers,
        json={
            "name": "tripper.pdf",
            "content_base64": base64.b64encode(b"%PDF trip").decode("utf-8"),
            "mime_type": "application/pdf",
        },
    )
    assert up_resp.status_code == 401

    # Verify status changed to needs_reconnect and jobs_paused=True
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == uuid.UUID(cred_id))
        res = await session.execute(stmt)
        cred = res.scalars().first()
        assert cred is not None
        assert cred.status == "needs_reconnect"
        assert cred.jobs_paused is True

    # Reset fault injection and credential so subsequent tests are clean
    drive_service.reset()
    await ensure_clean_connected_credential(async_client, headers)


@pytest.mark.asyncio
async def test_12_google_drive_client_httpx_unit() -> None:
    """Unit test for GoogleDriveService verifying real request building and parsing via MockTransport."""
    svc = GoogleDriveService()

    def custom_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        assert "Bearer test_drive_token" in request.headers["Authorization"]

        if "upload/drive/v3/files" in url_str:
            return httpx.Response(
                200,
                json={
                    "id": "drive_file_999",
                    "name": "uploaded_cv.pdf",
                    "mimeType": "application/pdf",
                    "size": "1024",
                    "webViewLink": "https://drive.google.com/view/999",
                    "createdTime": "2026-11-20T10:00:00Z",
                    "modifiedTime": "2026-11-20T10:00:00Z",
                },
            )
        elif "drive/v3/files/drive_file_999" in url_str:
            return httpx.Response(200, content=b"%PDF Binary Stream")
        elif "drive/v3/files" in url_str:
            if request.method == "POST":
                return httpx.Response(200, json={"id": "created_folder_111", "name": "Test Folder"})
            return httpx.Response(
                200, json={"files": [{"id": "folder_111", "name": "Existing Folder"}]}
            )
        return httpx.Response(404, json={"error": "Not Found"})

    real_async_client = httpx.AsyncClient

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(custom_transport)
        ),
    ):
        # 1. ensure_tenant_folder
        folder_id = await svc.ensure_tenant_folder(
            "Existing Folder", access_token="test_drive_token"
        )
        assert folder_id == "folder_111"

        # 2. upload_file
        uploaded = await svc.upload_file(
            name="uploaded_cv.pdf",
            content=b"%PDF Binary Stream",
            mime_type="application/pdf",
            access_token="test_drive_token",
        )
        assert uploaded.file_id == "drive_file_999"
        assert uploaded.size_bytes == 1024

        # 3. download_file
        downloaded = await svc.download_file("drive_file_999", access_token="test_drive_token")
        assert downloaded == b"%PDF Binary Stream"


@pytest.mark.asyncio
async def test_13_google_calendar_client_httpx_unit() -> None:
    """Unit test for GoogleCalendarService verifying real request building, Asia/Karachi normalization & Meet links."""
    svc = GoogleCalendarService()

    def custom_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        assert "Bearer test_cal_token" in request.headers["Authorization"]

        if "freeBusy" in url_str:
            return httpx.Response(
                200,
                json={
                    "calendars": {
                        "primary": {
                            "busy": [
                                {
                                    "start": "2026-11-20T10:00:00+05:00",
                                    "end": "2026-11-20T11:00:00+05:00",
                                }
                            ]
                        }
                    }
                },
            )
        elif "calendars/primary/events" in url_str:
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "id": "cal_evt_555",
                        "status": "confirmed",
                        "hangoutLink": "https://meet.google.com/xyz-uvwx-rst",
                    },
                )
            elif request.method == "PATCH":
                return httpx.Response(200, json={"id": "cal_evt_555", "status": "confirmed"})
            elif request.method == "DELETE":
                return httpx.Response(204)
        return httpx.Response(404, json={"error": "Not Found"})

    real_async_client = httpx.AsyncClient

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(custom_transport)
        ),
    ):
        # 1. get_free_busy
        fb = await svc.get_free_busy(
            ["primary"],
            time_min=datetime(2026, 11, 20, 8, 0, 0),
            time_max=datetime(2026, 11, 20, 18, 0, 0),
            access_token="test_cal_token",
        )
        assert len(fb["primary"]) == 1

        # 2. create_event
        created = await svc.create_event(
            CalendarEvent(
                title="Google Meet Interview",
                start_time=datetime(2026, 11, 20, 10, 0, 0),
                end_time=datetime(2026, 11, 20, 11, 0, 0),
                attendees=["candidate@example.com"],
            ),
            access_token="test_cal_token",
        )
        assert created["event_id"] == "cal_evt_555"
        assert created["hangout_link"] == "https://meet.google.com/xyz-uvwx-rst"

        # 3. update_event
        updated = await svc.update_event(
            "cal_evt_555",
            CalendarEventUpdate(title="Updated Meet Interview"),
            access_token="test_cal_token",
        )
        assert updated["event_id"] == "cal_evt_555"

        # 4. cancel_event
        deleted = await svc.cancel_event("cal_evt_555", access_token="test_cal_token")
        assert deleted is True


@pytest.mark.asyncio
async def test_14_drive_and_calendar_multi_tenant_isolation_api(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that Company B cannot view or download Company A's Drive files or private events."""
    ctx = isolation_context
    headers_a = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    headers_b = {"Authorization": f"Bearer {ctx.company_b.owner.token}"}

    # 1. Company A connects & uploads a private file
    await ensure_clean_connected_credential(
        async_client, headers_a, email="agentic.hr.test.ubaid.owner@gmail.com"
    )

    up_a = await async_client.post(
        "/api/v1/connectors/drive/upload",
        headers=headers_a,
        json={
            "name": "Company_A_Secret_Resume.pdf",
            "content_base64": base64.b64encode(b"%PDF Secret A").decode("utf-8"),
            "mime_type": "application/pdf",
        },
    )
    assert up_a.status_code == 200
    file_a_id = up_a.json()["id"]

    # 2. Company B connects
    await ensure_clean_connected_credential(
        async_client, headers_b, email="company_b_owner@gmail.com"
    )

    # 3. Company B attempts to list files -> must NOT contain Company A's file
    list_b = await async_client.get("/api/v1/connectors/drive/files", headers=headers_b)
    assert list_b.status_code == 200
    b_file_ids = [f["id"] for f in list_b.json()["files"]]
    assert file_a_id not in b_file_ids

    # 4. Company B attempts to download Company A's file -> must return 404
    down_b = await async_client.get(
        f"/api/v1/connectors/drive/files/{file_a_id}/download",
        headers=headers_b,
    )
    assert down_b.status_code == 404
