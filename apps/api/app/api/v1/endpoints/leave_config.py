"""
Stage 6 Leave & Work-Week Configuration REST API Endpoints.

Endpoints (mounted under /api/v1/leave/config):
- GET    /work-week
- PUT    /work-week
- GET    /types
- POST   /types
- PATCH  /types/{leave_type_id}
- DELETE /types/{leave_type_id}
- GET    /holidays
- POST   /holidays
- PATCH  /holidays/{holiday_id}
- DELETE /holidays/{holiday_id}
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
)
from app.db.session import get_db
from app.models.tenant import set_tenant_context
from app.schemas.leave_config import (
    HolidayCreateRequest,
    HolidayDeleteResponse,
    HolidayResponse,
    HolidayUpdateRequest,
    LeaveTypeCreateRequest,
    LeaveTypeDeleteResponse,
    LeaveTypeResponse,
    LeaveTypeUpdateRequest,
    WorkWeekResponse,
    WorkWeekUpdateRequest,
)
from app.services.leave_config_service import LeaveConfigService

router = APIRouter()


@router.get("/work-week", response_model=WorkWeekResponse)
async def get_work_week_config(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkWeekResponse:
    """Returns the tenant's work-week, probation, team coverage, and check-in configuration."""
    result = await LeaveConfigService.get_or_create_work_week(
        db=db,
        tenant_id=current_user.tenant_id,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.put("/work-week", response_model=WorkWeekResponse)
async def update_work_week_config(
    payload: WorkWeekUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> WorkWeekResponse:
    """Updates the tenant's work-week configuration. Restricted to Owner or HR Head."""
    result = await LeaveConfigService.update_work_week(
        db=db,
        tenant_id=current_user.tenant_id,
        payload=payload,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.get("/types", response_model=list[LeaveTypeResponse])
async def list_leave_types(
    include_inactive: bool = Query(False),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LeaveTypeResponse]:
    """Lists tenant leave types, auto-seeding default Pakistani SME types on first access."""
    result = await LeaveConfigService.list_leave_types(
        db=db,
        tenant_id=current_user.tenant_id,
        include_inactive=include_inactive,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.post(
    "/types",
    response_model=LeaveTypeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_leave_type(
    payload: LeaveTypeCreateRequest,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> LeaveTypeResponse:
    """Creates a new tenant leave type. Restricted to Owner or HR Head."""
    result = await LeaveConfigService.create_leave_type(
        db=db,
        tenant_id=current_user.tenant_id,
        payload=payload,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.patch("/types/{leave_type_id}", response_model=LeaveTypeResponse)
async def update_leave_type(
    leave_type_id: UUID,
    payload: LeaveTypeUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> LeaveTypeResponse:
    """Updates an existing leave type. Restricted to Owner or HR Head."""
    result = await LeaveConfigService.update_leave_type(
        db=db,
        tenant_id=current_user.tenant_id,
        leave_type_id=leave_type_id,
        payload=payload,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.delete("/types/{leave_type_id}", response_model=LeaveTypeDeleteResponse)
async def delete_leave_type(
    leave_type_id: UUID,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> LeaveTypeDeleteResponse:
    """Deletes or soft-deactivates a leave type. Restricted to Owner or HR Head."""
    result = await LeaveConfigService.delete_leave_type(
        db=db,
        tenant_id=current_user.tenant_id,
        leave_type_id=leave_type_id,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.get("/holidays", response_model=list[HolidayResponse])
async def list_holidays(
    year: int | None = Query(None, ge=2000, le=2100),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[HolidayResponse]:
    """Lists tenant public holidays, auto-seeding Pakistan fixed holidays on first access."""
    result = await LeaveConfigService.list_holidays(
        db=db,
        tenant_id=current_user.tenant_id,
        year=year,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.post(
    "/holidays",
    response_model=HolidayResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_holiday(
    payload: HolidayCreateRequest,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> HolidayResponse:
    """Creates a custom or default public holiday. Restricted to Owner or HR Head."""
    result = await LeaveConfigService.create_holiday(
        db=db,
        tenant_id=current_user.tenant_id,
        payload=payload,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.patch("/holidays/{holiday_id}", response_model=HolidayResponse)
async def update_holiday(
    holiday_id: UUID,
    payload: HolidayUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> HolidayResponse:
    """Updates an existing public holiday. Restricted to Owner or HR Head."""
    result = await LeaveConfigService.update_holiday(
        db=db,
        tenant_id=current_user.tenant_id,
        holiday_id=holiday_id,
        payload=payload,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result


@router.delete("/holidays/{holiday_id}", response_model=HolidayDeleteResponse)
async def delete_holiday(
    holiday_id: UUID,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> HolidayDeleteResponse:
    """Deletes a public holiday. Restricted to Owner or HR Head."""
    result = await LeaveConfigService.delete_holiday(
        db=db,
        tenant_id=current_user.tenant_id,
        holiday_id=holiday_id,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await set_tenant_context(db, current_user.tenant_id, current_user.clerk_user_id)
    return result
