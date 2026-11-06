import pytest
from fastapi.testclient import TestClient

from app.integrations.base import DriveFile
from app.integrations.factory import (
    get_drive_service,
    get_sheets_service,
)


def test_synthetic_company_profile(client: TestClient):
    response = client.get("/api/synthetic/company")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Acme Tech Solutions"
    assert "HR" in data["departments"]
    assert "Engineering" in data["departments"]
    assert len(data["test_accounts"]) == 6
    assert "agentic.hr.test.ubaid.owner@gmail.com" in data["test_accounts"]
    languages = [p["language"] for p in data["policy_documents"]]
    assert "en" in languages
    assert "ur" in languages


def test_synthetic_employees(client: TestClient):
    response = client.get("/api/synthetic/employees")
    assert response.status_code == 200
    employees = response.json()
    assert len(employees) >= 6
    emails = [e["email"] for e in employees]
    assert "agentic.hr.test.ubaid.owner@gmail.com" in emails
    assert "agentic.hr.test.ubaid.recruiter@gmail.com" in emails
    assert "agentic.hr.test.ubaid.hm@gmail.com" in emails


def test_fake_email_service(client: TestClient):
    response = client.post(
        "/api/synthetic/test-email",
        params={
            "to_email": "agentic.hr.test.ubaid.candidate1@gmail.com",
            "subject": "Interview Scheduled",
            "body": "Welcome to Stage 1 testing.",
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "success"
    assert result["result"]["fake"] is True
    assert result["result"]["details"]["to"] == "agentic.hr.test.ubaid.candidate1@gmail.com"


def test_fake_calendar_service(client: TestClient):
    response = client.post(
        "/api/synthetic/test-event",
        params={"title": "DocuVerify Technical Screening", "minutes_from_now": 30},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "success"
    assert result["result"]["fake"] is True
    assert "meet.google.com" in result["result"]["details"]["hangout_link"]


@pytest.mark.asyncio
async def test_fake_drive_service_direct():
    drive = get_drive_service()
    fake_cv = b"%PDF-1.4 Mock CV Content for DocuVerify OCR"
    uploaded: DriveFile = await drive.upload_file(
        name="candidate_cv.pdf",
        content=fake_cv,
        mime_type="application/pdf",
    )
    assert uploaded.file_id.startswith("fake_file_")
    assert uploaded.name == "candidate_cv.pdf"
    assert uploaded.size_bytes == len(fake_cv)


@pytest.mark.asyncio
async def test_fake_sheets_service_direct():
    sheets = get_sheets_service()
    directory = await sheets.get_employee_directory()
    assert len(directory) >= 6
    assert any(emp["employee_id"] == "EMP-001" for emp in directory)
