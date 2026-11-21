from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.security import TenantContext, get_current_tenant
from app.integrations.base import CalendarEvent, EmailMessage
from app.integrations.factory import (
    get_calendar_service,
    get_email_service,
    get_sheets_service,
)
from app.models.synthetic import CompanyProfile, EmployeeRecord
from app.services.synthetic_service import SyntheticDataService


def _require_non_production_synthetic() -> None:
    """Blocks synthetic helper routes outside non-production environments."""
    if not settings.is_non_production:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Synthetic endpoints are disabled in production.",
        )


router = APIRouter(
    prefix="/synthetic",
    tags=["synthetic"],
    dependencies=[Depends(_require_non_production_synthetic)],
)


@router.get("/company", response_model=CompanyProfile)
async def get_company_profile(
    tenant: TenantContext = Depends(get_current_tenant),
) -> CompanyProfile:
    """Return Acme Tech Solutions profile and policy documents (ADR 006 / synthetic-data-plan)."""
    return SyntheticDataService.get_company_profile()


@router.get("/employees", response_model=list[EmployeeRecord])
async def get_employees(
    tenant: TenantContext = Depends(get_current_tenant),
) -> list[EmployeeRecord]:
    """Return list of synthetic employees and test accounts."""
    return SyntheticDataService.get_synthetic_employees()


@router.get("/sheets-view")
async def get_sheets_directory(
    tenant: TenantContext = Depends(get_current_tenant),
) -> list[dict[str, Any]]:
    """Fetch employee directory from the Google Sheets synced view (ADR 005 & ADR 006)."""
    sheets_service = get_sheets_service()
    return await sheets_service.get_employee_directory()


@router.post("/test-email")
async def send_test_email(
    to_email: str = "agentic.hr.test.ubaid.owner@gmail.com",
    subject: str = "Agentic HR Stage 1 Verification",
    body: str = "This is a simulated verification email under USE_FAKE_INTEGRATIONS=true.",
    tenant: TenantContext = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Test sending an email through the integration layer (ADR 006)."""
    email_service = get_email_service()
    msg = EmailMessage(
        to_email=to_email,
        subject=subject,
        body_text=body,
    )
    result = await email_service.send_email(msg)
    return {
        "status": "success",
        "tenant_id": tenant.tenant_id,
        "result": result,
    }


@router.post("/test-event")
async def create_test_event(
    title: str = "Candidate Screening: DocuVerify Review",
    minutes_from_now: int = 60,
    tenant: TenantContext = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Test scheduling a calendar event through the integration layer (ADR 006)."""
    calendar_service = get_calendar_service()
    now = datetime.now(UTC)
    event = CalendarEvent(
        title=title,
        start_time=now + timedelta(minutes=minutes_from_now),
        end_time=now + timedelta(minutes=minutes_from_now + 30),
        attendees=[
            "agentic.hr.test.ubaid.hm@gmail.com",
            "agentic.hr.test.ubaid.candidate1@gmail.com",
        ],
        description="Stage 1 synthetic screening interview.",
    )
    result = await calendar_service.create_event(event)
    return {
        "status": "success",
        "tenant_id": tenant.tenant_id,
        "result": result,
    }
