"""
Departments CRUD REST API Endpoints for Agentic HR Core (Stage 4 Prompt 2).

Endpoints:
- GET /api/v1/departments
- POST /api/v1/departments
- PATCH /api/v1/departments/{department_id}
- DELETE /api/v1/departments/{department_id}
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_owner_manager_or_hr_head,
)
from app.db.session import get_db
from app.schemas.department import (
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
)
from app.services.department_service import DepartmentService

router = APIRouter()


@router.get("", response_model=list[DepartmentResponse])
async def list_departments(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DepartmentResponse]:
    """Lists all departments for the caller's tenant."""
    return await DepartmentService.list_departments(
        session=db,
        tenant_id=current_user.tenant_id,
    )


@router.post("", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    payload: DepartmentCreateRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    """Creates a new department in the caller's tenant."""
    result = await DepartmentService.create_department(
        session=db,
        tenant_id=current_user.tenant_id,
        payload=payload,
        actor_id=current_user.user_id,
    )
    await db.commit()
    return result


@router.patch("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: UUID,
    payload: DepartmentUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> DepartmentResponse:
    """Updates an existing department in the caller's tenant."""
    result = await DepartmentService.update_department(
        session=db,
        tenant_id=current_user.tenant_id,
        department_id=department_id,
        payload=payload,
        actor_id=current_user.user_id,
    )
    await db.commit()
    return result


@router.delete("/{department_id}")
async def delete_department(
    department_id: UUID,
    reassign_to_department_id: UUID | None = Query(None),
    force_null_reassign: bool = Query(False),
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """
    Deletes a department. Blocks deletion with HTTP 409 if employees belong to the department
    unless reassign_to_department_id or force_null_reassign=true is provided.
    """
    result = await DepartmentService.delete_department(
        session=db,
        tenant_id=current_user.tenant_id,
        department_id=department_id,
        actor_id=current_user.user_id,
        reassign_to_department_id=reassign_to_department_id,
        force_null_reassign=force_null_reassign,
    )
    await db.commit()
    return result
