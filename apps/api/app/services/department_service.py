"""
Departments CRUD Service for Agentic HR Core (Stage 4 Prompt 2).
Enforces tenant isolation (ADR 002), case-insensitive unique names, non-empty delete protection,
and immutable audit logging.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.employee import Employee
from app.schemas.department import (
    DepartmentCreateRequest,
    DepartmentResponse,
    DepartmentUpdateRequest,
)
from app.services.audit_service import write_audit


class DepartmentService:
    """Tenant-scoped CRUD operations for organizational departments."""

    @staticmethod
    async def list_departments(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
    ) -> list[DepartmentResponse]:
        """Lists all departments for the active tenant with per-department employee counts."""
        t_id = UUID(str(tenant_id))

        count_subq = (
            select(
                Employee.department_id.label("dept_id"),
                func.count(Employee.id).label("emp_count"),
            )
            .where(Employee.tenant_id == t_id)
            .group_by(Employee.department_id)
            .subquery()
        )

        stmt = (
            select(Department, func.coalesce(count_subq.c.emp_count, 0).label("employee_count"))
            .outerjoin(count_subq, Department.id == count_subq.c.dept_id)
            .where(Department.tenant_id == t_id)
            .order_by(func.lower(Department.name).asc())
        )
        rows = (await session.execute(stmt)).all()

        results: list[DepartmentResponse] = []
        for dept, emp_count in rows:
            resp = DepartmentResponse.model_validate(dept)
            resp.employee_count = int(emp_count or 0)
            results.append(resp)
        return results

    @staticmethod
    async def _validate_head_employee(
        session: AsyncSession,
        tenant_id: UUID,
        head_employee_id: UUID | None,
    ) -> None:
        if head_employee_id is None:
            return
        stmt = select(Employee.id).where(
            Employee.tenant_id == tenant_id,
            Employee.id == head_employee_id,
        )
        exists = (await session.execute(stmt)).scalar_one_or_none()
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Head employee '{head_employee_id}' not found in this tenant.",
            )

    @classmethod
    async def create_department(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        payload: DepartmentCreateRequest,
        actor_id: UUID | str | None = None,
    ) -> DepartmentResponse:
        """Creates a new department enforcing case-insensitive unique (tenant_id, lower(name))."""
        t_id = UUID(str(tenant_id))
        clean_name = payload.name.strip()
        if not clean_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Department name cannot be blank.",
            )

        dup_stmt = select(Department.id).where(
            Department.tenant_id == t_id,
            func.lower(Department.name) == clean_name.lower(),
        )
        if (await session.execute(dup_stmt)).scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"DEPARTMENT_ALREADY_EXISTS: Department '{clean_name}' already exists.",
            )

        await cls._validate_head_employee(session, t_id, payload.head_employee_id)

        dept = Department(
            tenant_id=t_id,
            name=clean_name,
            code=payload.code.strip() if payload.code else None,
            description=payload.description.strip() if payload.description else None,
            head_employee_id=payload.head_employee_id,
        )
        session.add(dept)
        await session.flush()

        await write_audit(
            session,
            action="department.created",
            target_type="department",
            target_id=dept.id,
            details={"name": dept.name, "code": dept.code},
            actor_id=actor_id,
            tenant_id=t_id,
        )

        resp = DepartmentResponse.model_validate(dept)
        resp.employee_count = 0
        return resp

    @classmethod
    async def update_department(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        department_id: UUID,
        payload: DepartmentUpdateRequest,
        actor_id: UUID | str | None = None,
    ) -> DepartmentResponse:
        """Updates a department within the active tenant."""
        t_id = UUID(str(tenant_id))
        stmt = select(Department).where(
            Department.tenant_id == t_id,
            Department.id == department_id,
        )
        dept = (await session.execute(stmt)).scalar_one_or_none()
        if not dept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Department '{department_id}' not found.",
            )

        updates = payload.model_dump(exclude_unset=True)
        if "name" in updates and updates["name"] is not None:
            clean_name = updates["name"].strip()
            if not clean_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Department name cannot be blank.",
                )
            dup_stmt = select(Department.id).where(
                Department.tenant_id == t_id,
                func.lower(Department.name) == clean_name.lower(),
                Department.id != department_id,
            )
            if (await session.execute(dup_stmt)).scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"DEPARTMENT_ALREADY_EXISTS: Department '{clean_name}' already exists.",
                )
            dept.name = clean_name

        if "code" in updates:
            dept.code = updates["code"].strip() if updates["code"] else None
        if "description" in updates:
            dept.description = updates["description"].strip() if updates["description"] else None
        if "head_employee_id" in updates:
            await cls._validate_head_employee(session, t_id, updates["head_employee_id"])
            dept.head_employee_id = updates["head_employee_id"]

        await session.flush()

        await write_audit(
            session,
            action="department.updated",
            target_type="department",
            target_id=dept.id,
            details={"updated_fields": sorted(updates.keys()), "name": dept.name},
            actor_id=actor_id,
            tenant_id=t_id,
        )

        emp_count_stmt = select(func.count(Employee.id)).where(
            Employee.tenant_id == t_id,
            Employee.department_id == dept.id,
        )
        emp_count = int((await session.execute(emp_count_stmt)).scalar() or 0)
        resp = DepartmentResponse.model_validate(dept)
        resp.employee_count = emp_count
        return resp

    @staticmethod
    async def delete_department(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        department_id: UUID,
        actor_id: UUID | str | None = None,
        reassign_to_department_id: UUID | None = None,
        force_null_reassign: bool = False,
    ) -> dict[str, object]:
        """
        Deletes a department. Blocks deletion with HTTP 409 (DEPARTMENT_NOT_EMPTY) if employees
        belong to the department unless `reassign_to_department_id` or `force_null_reassign=True`
        is explicitly provided.
        """
        t_id = UUID(str(tenant_id))
        stmt = select(Department).where(
            Department.tenant_id == t_id,
            Department.id == department_id,
        )
        dept = (await session.execute(stmt)).scalar_one_or_none()
        if not dept:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Department '{department_id}' not found.",
            )

        emp_count_stmt = select(func.count(Employee.id)).where(
            Employee.tenant_id == t_id,
            Employee.department_id == dept.id,
        )
        emp_count = int((await session.execute(emp_count_stmt)).scalar() or 0)

        reassigned_count = 0
        if emp_count > 0:
            if reassign_to_department_id is not None:
                if reassign_to_department_id == department_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="reassign_to_department_id cannot equal the department being deleted.",
                    )
                target_stmt = select(Department.id).where(
                    Department.tenant_id == t_id,
                    Department.id == reassign_to_department_id,
                )
                if not (await session.execute(target_stmt)).scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Reassignment target department '{reassign_to_department_id}' not found.",
                    )
                await session.execute(
                    update(Employee)
                    .where(
                        Employee.tenant_id == t_id,
                        Employee.department_id == department_id,
                    )
                    .values(department_id=reassign_to_department_id)
                )
                reassigned_count = emp_count
            elif force_null_reassign:
                await session.execute(
                    update(Employee)
                    .where(
                        Employee.tenant_id == t_id,
                        Employee.department_id == department_id,
                    )
                    .values(department_id=None)
                )
                reassigned_count = emp_count
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"DEPARTMENT_NOT_EMPTY: Cannot delete department '{dept.name}' because "
                        f"{emp_count} employee(s) are assigned to it. Reassign employees via "
                        "reassign_to_department_id or pass force_null_reassign=true."
                    ),
                )

        dept_name = dept.name
        await session.delete(dept)
        await session.flush()

        await write_audit(
            session,
            action="department.deleted",
            target_type="department",
            target_id=department_id,
            details={
                "name": dept_name,
                "reassigned_employees": reassigned_count,
                "reassign_to_department_id": (
                    str(reassign_to_department_id) if reassign_to_department_id else None
                ),
                "force_null_reassign": force_null_reassign,
            },
            actor_id=actor_id,
            tenant_id=t_id,
        )

        return {
            "status": "deleted",
            "id": str(department_id),
            "reassigned_employees": reassigned_count,
        }
