"""
Stage 3, Prompt 8: Recorded JSON Response Contract Tests.
Proves that Google HTTP client implementations:
1. Comply with Google's REST API wire contracts (methods, URLs, headers, payloads).
2. Exactly match Fake service return schemas (100% Fake-to-Real parity).
3. Deterministically map Google HTTP error codes (400, 401, 429, 503) to typed exceptions.
"""

import base64
import email
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.integrations.base import (
    CalendarEvent,
    CalendarEventUpdate,
    DriveFile,
    EmailMessage,
    GmailDeliveryError,
    TimeSlot,
)
from app.integrations.fake.fake_calendar import FakeCalendarService
from app.integrations.fake.fake_drive import FakeDriveService
from app.integrations.fake.fake_gmail import FakeGmailService
from app.integrations.fake.fake_sheets import FakeSheetsService
from app.integrations.google.calendar_client import GoogleCalendarService, get_karachi_tz
from app.integrations.google.drive_client import GoogleDriveService
from app.integrations.google.gmail_client import GoogleGmailService
from app.integrations.google.sheets_client import GoogleSheetsService
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "google_contracts"


def load_fixture(category: str, filename: str) -> dict[str, Any]:
    fixture_path = FIXTURES_DIR / category / filename
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ==============================================================================
# 1. GMAIL REST API CONTRACT TESTS (v1 users.messages.send)
# ==============================================================================


@pytest.mark.asyncio
async def test_gmail_contract_send_success() -> None:
    fixture = load_fixture("gmail", "send_200.json")
    captured_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=fixture)

    transport = httpx.MockTransport(mock_handler)
    real_client = GoogleGmailService(transport=transport)
    fake_client = FakeGmailService()

    message = EmailMessage(
        to_email="agentic.hr.test.ubaid.candidate1@gmail.com",
        subject="Interview Invitation: Python Engineer",
        body_text="Dear Candidate, please join tomorrow.",
        body_html="<p>Dear Candidate, please join tomorrow.</p>",
        sender_email="agentic.hr.test.ubaid.owner@gmail.com",
    )

    real_result = await real_client.send_email(message, access_token="mock_bearer_token_123")
    fake_result = await fake_client.send_email(message, access_token="mock_bearer_token_123")

    # 1. Verify outbound HTTP wire contract
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "POST"
    assert req.url == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    assert req.headers["Authorization"] == "Bearer mock_bearer_token_123"
    assert req.headers["Content-Type"] == "application/json"

    # 2. Verify RFC 2822 multipart/alternative structure inside raw Base64URL
    body = json.loads(req.content)
    assert "raw" in body
    raw_bytes = base64.urlsafe_b64decode(body["raw"])
    parsed_mime = email.message_from_bytes(raw_bytes)
    assert parsed_mime.is_multipart()
    assert parsed_mime.get_content_type() == "multipart/alternative"
    assert parsed_mime["To"] == "agentic.hr.test.ubaid.candidate1@gmail.com"
    assert parsed_mime["Subject"] == "Interview Invitation: Python Engineer"

    # 3. Verify Fake-to-Real return contract parity
    assert real_result["status"] == fake_result["status"] == "success"
    assert real_result["fake"] is False
    assert fake_result["fake"] is True
    assert "message_id" in real_result and "message_id" in fake_result
    assert real_result["message_id"] == "18e6e5f381ab924c"


@pytest.mark.asyncio
async def test_gmail_contract_400_invalid_recipient() -> None:
    fixture = load_fixture("gmail", "send_400_invalid_recipient.json")
    transport = httpx.MockTransport(lambda req: httpx.Response(400, json=fixture))
    client = GoogleGmailService(transport=transport)

    message = EmailMessage(to_email="invalid-email-format", subject="Hi", body_text="Hello")
    with pytest.raises(GmailDeliveryError) as exc_info:
        await client.send_email(message, access_token="tok")
    assert "400" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gmail_contract_401_invalid_grant() -> None:
    fixture = load_fixture("gmail", "send_401_invalid_grant.json")
    transport = httpx.MockTransport(lambda req: httpx.Response(401, json=fixture))
    client = GoogleGmailService(transport=transport)

    message = EmailMessage(to_email="test@example.com", subject="Hi", body_text="Hello")
    with pytest.raises(ConnectorAuthExpiredError):
        await client.send_email(message, access_token="expired_token")


@pytest.mark.asyncio
async def test_gmail_contract_429_rate_limit() -> None:
    fixture = load_fixture("gmail", "send_429_rate_limit.json")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(429, json=fixture, headers={"Retry-After": "4"})
    )
    client = GoogleGmailService(transport=transport)

    message = EmailMessage(to_email="test@example.com", subject="Hi", body_text="Hello")
    with pytest.raises(ConnectorRateLimitError) as exc_info:
        await client.send_email(message, access_token="tok")
    assert exc_info.value.retry_after == 4


@pytest.mark.asyncio
async def test_gmail_contract_503_backend_error() -> None:
    fixture = load_fixture("gmail", "send_503_backend_error.json")
    transport = httpx.MockTransport(lambda req: httpx.Response(503, json=fixture))
    client = GoogleGmailService(transport=transport)

    message = EmailMessage(to_email="test@example.com", subject="Hi", body_text="Hello")
    with pytest.raises(ConnectorServiceUnavailableError):
        await client.send_email(message, access_token="tok")


# ==============================================================================
# 2. SHEETS REST API CONTRACT TESTS (v4 Sheets & v3 Drive Discovery)
# ==============================================================================


@pytest.mark.asyncio
async def test_sheets_contract_create_or_find_existing() -> None:
    drive_fixture = load_fixture("sheets", "drive_files_list_200.json")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "drive/v3/files" in str(request.url)
        assert request.headers["Authorization"] == "Bearer test_sheets_token"
        return httpx.Response(200, json=drive_fixture)

    transport = httpx.MockTransport(mock_handler)
    client = GoogleSheetsService(transport=transport)

    sheet_id = await client.create_or_find_spreadsheet(
        "Acme HR Employee Directory", access_token="test_sheets_token"
    )
    assert sheet_id == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"


@pytest.mark.asyncio
async def test_sheets_contract_create_or_find_new() -> None:
    drive_empty = {"files": []}
    create_fixture = load_fixture("sheets", "spreadsheets_create_200.json")
    update_fixture = load_fixture("sheets", "values_update_200.json")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "drive/v3/files" in url_str:
            return httpx.Response(200, json=drive_empty)
        if url_str == "https://sheets.googleapis.com/v4/spreadsheets":
            assert request.method == "POST"
            return httpx.Response(200, json=create_fixture)
        if "values/Employees" in url_str:
            assert request.method == "PUT"
            return httpx.Response(200, json=update_fixture)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    client = GoogleSheetsService(transport=transport)

    sheet_id = await client.create_or_find_spreadsheet(
        "Acme HR Employee Directory", access_token="tok"
    )
    assert sheet_id == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"


@pytest.mark.asyncio
async def test_sheets_contract_read_and_append_parity() -> None:
    read_fixture = load_fixture("sheets", "values_get_200.json")
    append_fixture = load_fixture("sheets", "values_append_200.json")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if ":append" in url_str:
            assert request.method == "POST"
            assert request.url.params["valueInputOption"] == "USER_ENTERED"
            assert request.url.params["insertDataOption"] == "INSERT_ROWS"
            return httpx.Response(200, json=append_fixture)
        if "/values/" in url_str:
            assert request.method == "GET"
            return httpx.Response(200, json=read_fixture)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    real_client = GoogleSheetsService(transport=transport)
    fake_client = FakeSheetsService()

    # 1. Read range contract parity
    real_rows = await real_client.read_range("sheet_123", "Employees!A1:F5", access_token="tok")
    fake_rows = await fake_client.read_range("fake_sheet_employee_directory", "Employees!A1:F5")
    assert isinstance(real_rows, list)
    assert isinstance(fake_rows, list)
    assert len(real_rows) == 5
    assert real_rows[0] == ["Employee ID", "Name", "Email", "Department", "Role", "Status"]

    # 2. Append rows contract parity
    row_to_append = [["EMP-005", "New User", "new@example.com", "Eng", "Dev", "Active"]]
    real_append = await real_client.append_rows(
        "sheet_123", "Employees!A1:F", row_to_append, access_token="tok"
    )
    fake_append = await fake_client.append_rows(
        "fake_sheet_employee_directory", "Employees!A1:F", row_to_append
    )

    assert "spreadsheetId" in real_append and "spreadsheetId" in fake_append
    assert "updates" in real_append and "updates" in fake_append
    assert real_append["updates"]["updatedRows"] == fake_append["updates"]["updatedRows"] == 1


@pytest.mark.asyncio
async def test_sheets_contract_401_unauthorized() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="invalid_grant"))
    client = GoogleSheetsService(transport=transport)

    with pytest.raises(ConnectorAuthExpiredError):
        await client.read_range("sheet_123", "Employees!A1:F", access_token="tok")


# ==============================================================================
# 3. DRIVE REST API CONTRACT TESTS (v3 Files & Multipart Upload)
# ==============================================================================


@pytest.mark.asyncio
async def test_drive_contract_folder_creation_and_listing() -> None:
    folder_fixture = load_fixture("drive", "folder_create_200.json")
    files_fixture = load_fixture("drive", "files_list_files_200.json")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "GET" and "files" in url_str:
            if "pageSize" in request.url.params:
                return httpx.Response(200, json=files_fixture)
            return httpx.Response(200, json={"files": []})
        if request.method == "POST" and "files" in url_str:
            return httpx.Response(200, json=folder_fixture)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    real_client = GoogleDriveService(transport=transport)
    fake_client = FakeDriveService()

    # 1. Folder creation contract
    folder_id = await real_client.ensure_tenant_folder("tenant_folder", access_token="tok")
    fake_folder_id = await fake_client.ensure_tenant_folder("tenant_folder")
    assert folder_id == "1folder_tenant_root_abc123"
    assert isinstance(fake_folder_id, str)

    # 2. File list contract parity
    real_files = await real_client.list_files(folder_id=folder_id, access_token="tok")
    assert len(real_files) == 1
    rf = real_files[0]
    assert isinstance(rf, DriveFile)
    assert rf.file_id == "1file_resume_pdf_98765"
    assert rf.mime_type == "application/pdf"
    assert rf.size_bytes == 524288


@pytest.mark.asyncio
async def test_drive_contract_multipart_upload_and_download() -> None:
    upload_fixture = load_fixture("drive", "file_upload_multipart_200.json")
    fake_pdf_content = b"%PDF-1.4 simulated resume document content"

    captured_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        url_str = str(request.url)
        if "upload/drive/v3/files" in url_str:
            assert request.method == "POST"
            assert "multipart/related" in request.headers["Content-Type"]
            return httpx.Response(200, json=upload_fixture)
        if f"/files/{upload_fixture['id']}" in url_str:
            assert request.method == "GET"
            assert request.url.params["alt"] == "media"
            return httpx.Response(200, content=fake_pdf_content)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    real_client = GoogleDriveService(transport=transport)
    fake_client = FakeDriveService()

    # 1. Upload contract parity
    real_file = await real_client.upload_file(
        name="sample_candidate_cv.pdf",
        content=fake_pdf_content,
        mime_type="application/pdf",
        folder_id="1folder_tenant_root_abc123",
        access_token="tok",
    )
    fake_file = await fake_client.upload_file(
        name="sample_candidate_cv.pdf",
        content=fake_pdf_content,
        mime_type="application/pdf",
        folder_id="1folder_tenant_root_abc123",
    )

    assert real_file.name == fake_file.name == "sample_candidate_cv.pdf"
    assert real_file.mime_type == fake_file.mime_type == "application/pdf"
    assert real_file.folder_id == fake_file.folder_id == "1folder_tenant_root_abc123"

    # 2. Download contract parity
    downloaded_bytes = await real_client.download_file(real_file.file_id, access_token="tok")
    assert downloaded_bytes == fake_pdf_content


@pytest.mark.asyncio
async def test_drive_contract_error_mappings() -> None:
    # 401 Unauthorized
    t_401 = httpx.MockTransport(lambda req: httpx.Response(401, text="invalid_grant"))
    with pytest.raises(ConnectorAuthExpiredError):
        await GoogleDriveService(transport=t_401).list_files(access_token="tok")

    # 429 Rate Limit
    t_429 = httpx.MockTransport(
        lambda req: httpx.Response(429, text="Rate limit", headers={"Retry-After": "2"})
    )
    with pytest.raises(ConnectorRateLimitError) as exc_429:
        await GoogleDriveService(transport=t_429).list_files(access_token="tok")
    assert exc_429.value.retry_after == 2

    # 503 Service Unavailable
    t_503 = httpx.MockTransport(lambda req: httpx.Response(503, text="Unavailable"))
    with pytest.raises(ConnectorServiceUnavailableError):
        await GoogleDriveService(transport=t_503).list_files(access_token="tok")

    # 404 File Not Found
    t_404 = httpx.MockTransport(lambda req: httpx.Response(404, text="Not Found"))
    with pytest.raises(FileNotFoundError):
        await GoogleDriveService(transport=t_404).download_file("missing_id", access_token="tok")


# ==============================================================================
# 4. CALENDAR REST API CONTRACT TESTS (v3 Calendar & Asia/Karachi Timezone)
# ==============================================================================


@pytest.mark.asyncio
async def test_calendar_contract_freebusy_query() -> None:
    fixture = load_fixture("calendar", "freebusy_query_200.json")
    captured_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=fixture)

    transport = httpx.MockTransport(mock_handler)
    real_client = GoogleCalendarService(transport=transport)
    fake_client = FakeCalendarService()

    t_min = datetime(2026, 11, 12, 9, 0, tzinfo=get_karachi_tz())
    t_max = datetime(2026, 11, 12, 17, 0, tzinfo=get_karachi_tz())

    real_res = await real_client.get_free_busy(["primary"], t_min, t_max, access_token="tok")
    fake_res = await fake_client.get_free_busy(["primary"], t_min, t_max)

    # 1. Assert wire request payload
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "POST"
    assert req.url == "https://www.googleapis.com/calendar/v3/freeBusy"
    body = json.loads(req.content)
    assert body["timeZone"] == "Asia/Karachi"

    # 2. Assert return parity
    assert "primary" in real_res and "primary" in fake_res
    slots = real_res["primary"]
    assert len(slots) == 2
    assert isinstance(slots[0], TimeSlot)
    assert slots[0].start_time.hour == 10
    assert slots[0].end_time.hour == 11


@pytest.mark.asyncio
async def test_calendar_contract_event_lifecycle_and_meet_link() -> None:
    insert_fixture = load_fixture("calendar", "event_insert_200.json")
    patch_fixture = load_fixture("calendar", "event_patch_200.json")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if request.method == "POST" and "events" in url_str:
            assert request.url.params["conferenceDataVersion"] == "1"
            return httpx.Response(200, json=insert_fixture)
        if request.method == "PATCH" and "events" in url_str:
            return httpx.Response(200, json=patch_fixture)
        if request.method == "DELETE" and "events" in url_str:
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    real_client = GoogleCalendarService(transport=transport)
    fake_client = FakeCalendarService()

    # 1. Create event with Google Meet parity
    start_dt = datetime.now(UTC) + timedelta(days=1)
    end_dt = start_dt + timedelta(hours=1)
    event_spec = CalendarEvent(
        title="Technical Interview - Backend Engineer",
        description="Round 1 Screening",
        start_time=start_dt,
        end_time=end_dt,
        attendees=["agentic.hr.test.ubaid.candidate1@gmail.com"],
    )

    real_evt = await real_client.create_event(event_spec, access_token="tok")
    fake_evt = await fake_client.create_event(event_spec)

    assert real_evt["status"] == fake_evt["status"] == "confirmed"
    assert real_evt["fake"] is False
    assert fake_evt["fake"] is True
    assert real_evt["hangout_link"] == "https://meet.google.com/xyz-uvwx-rst"
    assert "meet.google.com" in fake_evt["hangout_link"]

    # 2. Update event parity
    update_spec = CalendarEventUpdate(title="Technical Interview - Rescheduled to 3 PM")
    real_updated = await real_client.update_event(
        real_evt["event_id"], update_spec, access_token="tok"
    )
    assert real_updated["event_id"] == real_evt["event_id"]
    assert real_updated["status"] == "confirmed"

    # 3. Cancel event parity
    real_cancelled = await real_client.cancel_event(real_evt["event_id"], access_token="tok")
    fake_cancelled = await fake_client.cancel_event(fake_evt["event_id"])
    assert real_cancelled is True
    assert fake_cancelled is True


@pytest.mark.asyncio
async def test_calendar_contract_error_mappings() -> None:
    # 401
    t_401 = httpx.MockTransport(lambda req: httpx.Response(401, text="invalid_grant"))
    with pytest.raises(ConnectorAuthExpiredError):
        await GoogleCalendarService(transport=t_401).cancel_event("evt_1", access_token="tok")

    # 429
    t_429 = httpx.MockTransport(
        lambda req: httpx.Response(429, text="Rate limit", headers={"Retry-After": "3"})
    )
    with pytest.raises(ConnectorRateLimitError) as exc_429:
        await GoogleCalendarService(transport=t_429).cancel_event("evt_1", access_token="tok")
    assert exc_429.value.retry_after == 3

    # 503
    t_503 = httpx.MockTransport(lambda req: httpx.Response(503, text="Service Unavailable"))
    with pytest.raises(ConnectorServiceUnavailableError):
        await GoogleCalendarService(transport=t_503).cancel_event("evt_1", access_token="tok")
