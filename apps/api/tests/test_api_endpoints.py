"""
Backend API endpoint verification tests using HTTPX.
Tests synthetic business endpoints, tenant context validation, and request ID tracking.
"""

import httpx
import pytest


@pytest.mark.asyncio
async def test_get_company_profile_httpx(async_client: httpx.AsyncClient) -> None:
    """Verifies that GET /api/synthetic/company returns 200 with Acme Tech Solutions data."""
    headers = {"X-Tenant-ID": "acme-tech-01"}
    response = await async_client.get("/api/synthetic/company", headers=headers)

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers

    data = response.json()
    assert data["name"] == "Acme Tech Solutions"
    assert data["domain"] == "acmetech.internal"
    assert data["total_employees"] == 40
    assert len(data["test_accounts"]) == 6
    assert len(data["policy_documents"]) == 6


@pytest.mark.asyncio
async def test_get_employees_httpx(async_client: httpx.AsyncClient) -> None:
    """Verifies that GET /api/synthetic/employees returns synthetic employee records."""
    headers = {"X-Tenant-ID": "acme-tech-01"}
    response = await async_client.get("/api/synthetic/employees", headers=headers)

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers

    employees = response.json()
    assert isinstance(employees, list)
    assert len(employees) >= 6

    first_employee = employees[0]
    assert "employee_id" in first_employee
    assert "name" in first_employee
    assert "email" in first_employee
    assert "department" in first_employee
    assert "role" in first_employee
    assert "status" in first_employee


@pytest.mark.asyncio
async def test_send_test_email_httpx(async_client: httpx.AsyncClient) -> None:
    """Verifies that POST /api/synthetic/test-email dispatches a simulated email."""
    headers = {"X-Tenant-ID": "acme-tech-01"}
    params = {
        "to_email": "agentic.hr.test.ubaid.candidate1@gmail.com",
        "subject": "Automated Pytest HTTPX Verification",
        "body": "Synthetic email sent via async HTTPX test suite.",
    }
    response = await async_client.post("/api/synthetic/test-email", headers=headers, params=params)

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers

    data = response.json()
    assert data["status"] == "success"
    assert data["tenant_id"] == "acme-tech-01"
    assert "result" in data


@pytest.mark.asyncio
async def test_sheets_view_httpx(async_client: httpx.AsyncClient) -> None:
    """Verifies that GET /api/synthetic/sheets-view returns synchronized directory view."""
    headers = {"X-Tenant-ID": "acme-tech-01"}
    response = await async_client.get("/api/synthetic/sheets-view", headers=headers)

    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list)
    assert len(rows) > 0
