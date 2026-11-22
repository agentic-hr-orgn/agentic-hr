"""
Stage 6 Leave Management REST API Endpoints (Prompt 3).

Endpoints (mounted under /api/v1/leave):
- POST /intake
- POST /requests
- GET  /balances/me
- GET  /balances
- GET  /requests/me
- GET  /inbox
- GET  /calendar
- GET  /overview
- POST /requests/{request_id}/decide
- POST /requests/{request_id}/cancel
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
    require_owner_manager_or_hr_head,
)
from app.db.session import get_db
from app.models.tenant import set_tenant_context
from app.schemas.leave import (
    LeaveBalanceItemResponse,
    LeaveCancelRequest,
    LeaveDecisionRequest,
    LeaveIntakeRequest,
    LeaveIntakeResponse,
    LeaveOverviewResponse,
    LeaveRequestCreateRequest,
    LeaveRequestResponse,
    TeamCalendarItemResponse,
)
from app.services.leave_intake_agent import parse_leave_intake
from app.services.leave_service import LeaveService

router = APIRouter()


@router.post(
    "/intake",
    response_model=LeaveIntakeResponse,
    dependencies=[Depends(RateLimiter(requests=30, window_seconds=60, scope="leave.intake"))],
)
async def leave_intake_chat(
    payload: LeaveIntakeRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LeaveIntakeResponse:
    """
    Conversational bilingual leave intake agent. Asks at most ONE focused clarifying
    question if incomplete, or runs deterministic `check_leave` and returns a `confirm_card`.
    """
    result = await parse_leave_intake(
        db=db,
        actor=current_user,
        message=payload.message,
        conversation_context=payload.conversation_context,
        employee_id=payload.employee_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.post(
    "/requests",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_leave_request(
    payload: LeaveRequestCreateRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LeaveRequestResponse:
    """
    Submits a leave request using serialized `SELECT ... FOR UPDATE` balance locking,
    reserves balance, optionally creates a Stage 2 `leave.exception` approval on
    policy violation, and starts durable `LeaveRequestWorkflow`.
    """
    return await LeaveService.submit_leave_request(db=db, actor=current_user, payload=payload)


@router.get("/balances/me", response_model=list[LeaveBalanceItemResponse])
async def get_my_leave_balances(
    year: int | None = Query(None, ge=2000, le=2100),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LeaveBalanceItemResponse]:
    """Returns the caller's leave balances for the requested (or current PKT) year."""
    result = await LeaveService.get_balances(db=db, actor=current_user, year=year, self_only=True)
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.get("/balances", response_model=list[LeaveBalanceItemResponse])
async def list_all_leave_balances(
    employee_id: UUID | None = Query(None),
    year: int | None = Query(None, ge=2000, le=2100),
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> list[LeaveBalanceItemResponse]:
    """HR Head / Owner view of tenant leave balances."""
    return await LeaveService.get_balances(
        db=db,
        actor=current_user,
        employee_id=employee_id,
        year=year,
        self_only=False,
    )


@router.get("/requests/me", response_model=list[LeaveRequestResponse])
async def list_my_leave_requests(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LeaveRequestResponse]:
    """Lists the caller's own submitted leave requests."""
    return await LeaveService.list_my_requests(db=db, actor=current_user)


@router.get("/inbox", response_model=list[LeaveRequestResponse])
async def list_leave_approval_inbox(
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> list[LeaveRequestResponse]:
    """
    Lists pending leave requests assigned to the caller (or escalated backup approver).
    Owner and HR Head see all pending requests in the tenant.
    """
    return await LeaveService.list_manager_inbox(db=db, actor=current_user)


@router.get("/calendar", response_model=list[TeamCalendarItemResponse])
async def list_team_leave_calendar(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    department_id: UUID | None = Query(None),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TeamCalendarItemResponse]:
    """Lists approved and pending team leaves for calendar visibility."""
    return await LeaveService.list_team_calendar(
        db=db,
        actor=current_user,
        start_date=start_date,
        end_date=end_date,
        department_id=department_id,
    )


@router.get("/overview", response_model=LeaveOverviewResponse)
async def get_hr_leave_overview(
    status_filter: str | None = Query(None, alias="status"),
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> LeaveOverviewResponse:
    """HR Head / Owner overview metrics and full tenant leave request log."""
    return await LeaveService.get_hr_overview(
        db=db,
        actor=current_user,
        status_filter=status_filter,
    )


@router.post("/requests/{request_id}/decide", response_model=LeaveRequestResponse)
async def decide_leave_request(
    request_id: UUID,
    payload: LeaveDecisionRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> LeaveRequestResponse:
    """Approves or rejects a pending leave request (strict approver/escalated/HR RBAC)."""
    return await LeaveService.decide_leave_request(
        db=db,
        actor=current_user,
        request_id=request_id,
        payload=payload,
    )


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestResponse)
async def cancel_leave_request(
    request_id: UUID,
    payload: LeaveCancelRequest | None = None,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LeaveRequestResponse:
    """Cancels a pending request (releases reservation) or approved request (reverses attendance/balance)."""
    reason = payload.reason if payload else None
    return await LeaveService.cancel_leave_request(
        db=db,
        actor=current_user,
        request_id=request_id,
        reason=reason,
    )
