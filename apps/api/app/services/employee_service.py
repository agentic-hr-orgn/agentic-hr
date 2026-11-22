"""
Employee Directory, Role-Based Encrypted PII Profiles, Org Chart, and PII Crypto Service
for Agentic HR Core (Stage 4 Prompts 1 & 4).

Complies with:
- ADR 002 (Multi-Tenancy with forced PostgreSQL RLS)
- Stage 2 Phase 9 AES-256-GCM Envelope Encryption with canonical 4-field AAD:
    (tenant_id, "employee", employee_id, "cnic" | "bank_account")
- Stage 4 Prompt 1 `public.pub_employees` (security_invoker=true, security_barrier=true)
"""

from __future__ import annotations

import math
import re
import uuid
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import (
    EncryptedFieldPayload,
    EncryptionContext,
    KeyProvider,
    decrypt_field,
    encrypt_field,
)
from app.schemas.employee import (
    EmployeeCreateRequest,
    EmployeeListItem,
    EmployeeListResponse,
    EmployeeProfileResponse,
    EmployeeUpdateRequest,
    OrgChartNode,
    OrgChartResponse,
)
from app.services.audit_service import write_audit
from app.services.leave_config_service import initialize_employee_leave_balances
from app.services.sheets_sync_service import SheetsSyncService

if TYPE_CHECKING:
    from app.core.security import AuthenticatedUserContext

EMPLOYEE_ENTITY_TYPE = "employee"
CNIC_FIELD_NAME = "cnic"
BANK_ACCOUNT_FIELD_NAME = "bank_account"

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
CNIC_REGEX = re.compile(r"^(\d{5}-\d{7}-\d|\d{13})$")


class EmployeeService:
    """
    Domain helper and service layer for Employee records, directory search,
    role-based encrypted PII profiles, org chart hierarchy, and AES-256-GCM PII protection.
    Never logs raw CNICs, bank accounts, or encryption keys.
    """

    # ------------------------------------------------------------------
    # AES-256-GCM PII Encryption, Decryption & Masking Helpers (Prompt 1)
    # ------------------------------------------------------------------

    @staticmethod
    def build_pii_context(
        tenant_id: UUID | str,
        employee_id: UUID | str,
        field_name: str,
    ) -> EncryptionContext:
        """
        Builds canonical 4-field EncryptionContext for an employee sensitive field.
        """
        if field_name not in (CNIC_FIELD_NAME, BANK_ACCOUNT_FIELD_NAME):
            raise ValueError(f"Unsupported employee PII field_name: '{field_name}'")
        return EncryptionContext(
            tenant_id=tenant_id,
            entity_type=EMPLOYEE_ENTITY_TYPE,
            record_id=employee_id,
            field_name=field_name,
        )

    @classmethod
    def encrypt_cnic(
        cls,
        tenant_id: UUID | str,
        employee_id: UUID | str,
        plaintext_cnic: str,
        key_id: str | None = None,
        key_provider: KeyProvider | None = None,
    ) -> str:
        """Encrypts an employee CNIC bound to (tenant_id, 'employee', employee_id, 'cnic')."""
        clean = plaintext_cnic.strip() if isinstance(plaintext_cnic, str) else ""
        if not clean:
            raise ValueError("CNIC plaintext must be a non-empty string.")
        ctx = cls.build_pii_context(tenant_id, employee_id, CNIC_FIELD_NAME)
        return encrypt_field(clean, ctx, key_id=key_id, key_provider=key_provider)

    @classmethod
    def decrypt_cnic(
        cls,
        tenant_id: UUID | str,
        employee_id: UUID | str,
        encrypted_cnic: str | EncryptedFieldPayload,
        key_provider: KeyProvider | None = None,
    ) -> str:
        """Decrypts an employee CNIC bound to (tenant_id, 'employee', employee_id, 'cnic')."""
        ctx = cls.build_pii_context(tenant_id, employee_id, CNIC_FIELD_NAME)
        return decrypt_field(encrypted_cnic, ctx, key_provider=key_provider)

    @classmethod
    def encrypt_bank_account(
        cls,
        tenant_id: UUID | str,
        employee_id: UUID | str,
        plaintext_bank_account: str,
        key_id: str | None = None,
        key_provider: KeyProvider | None = None,
    ) -> str:
        """
        Encrypts an employee bank account / IBAN bound to
        (tenant_id, 'employee', employee_id, 'bank_account').
        """
        clean = plaintext_bank_account.strip() if isinstance(plaintext_bank_account, str) else ""
        if not clean:
            raise ValueError("Bank account plaintext must be a non-empty string.")
        ctx = cls.build_pii_context(tenant_id, employee_id, BANK_ACCOUNT_FIELD_NAME)
        return encrypt_field(clean, ctx, key_id=key_id, key_provider=key_provider)

    @classmethod
    def decrypt_bank_account(
        cls,
        tenant_id: UUID | str,
        employee_id: UUID | str,
        encrypted_bank_account: str | EncryptedFieldPayload,
        key_provider: KeyProvider | None = None,
    ) -> str:
        """
        Decrypts an employee bank account / IBAN bound to
        (tenant_id, 'employee', employee_id, 'bank_account').
        """
        ctx = cls.build_pii_context(tenant_id, employee_id, BANK_ACCOUNT_FIELD_NAME)
        return decrypt_field(encrypted_bank_account, ctx, key_provider=key_provider)

    @staticmethod
    def mask_cnic(cnic: str | None) -> str | None:
        """
        Masks a Pakistani CNIC to '*****-*******-<last_char>' (e.g. '42101-1234567-1' -> '*****-*******-1').
        Returns None if cnic is None or empty.
        """
        if not cnic or not isinstance(cnic, str):
            return None
        clean = cnic.strip()
        if not clean:
            return None
        alnum = [ch for ch in clean if ch.isalnum()]
        last_char = alnum[-1] if alnum else clean[-1]
        return f"*****-*******-{last_char}"

    @staticmethod
    def mask_bank_account(bank_account: str | None) -> str | None:
        """
        Masks a bank account or IBAN to '****<last_4>' (e.g. 'PK36ACME000100001234' -> '****1234').
        Returns None if bank_account is None or empty.
        """
        if not bank_account or not isinstance(bank_account, str):
            return None
        clean = "".join(bank_account.split())
        if not clean:
            return None
        if len(clean) <= 4:
            return "****"
        return f"****{clean[-4:]}"

    # ------------------------------------------------------------------
    # Role-Based Authorization Helpers (Prompt 4)
    # ------------------------------------------------------------------

    @staticmethod
    def can_edit_sensitive_pii(actor: AuthenticatedUserContext) -> bool:
        """Only owner, hr_head, or payroll_admin may modify CNIC or bank account."""
        return bool(actor.role == "owner" or actor.is_hr_head or actor.is_payroll_admin)

    @staticmethod
    def can_view_sensitive_pii(
        actor: AuthenticatedUserContext,
        employee_user_id: UUID | None,
    ) -> bool:
        """
        Owner, hr_head, payroll_admin, or the employee themselves (self-view)
        may view decrypted CNIC and bank account.
        """
        if actor.role == "owner" or actor.is_hr_head or actor.is_payroll_admin:
            return True
        return bool(employee_user_id is not None and str(employee_user_id) == str(actor.user_id))

    @staticmethod
    def can_manage_employees(actor: AuthenticatedUserContext) -> bool:
        """Owner, manager, or hr_head may create or update non-sensitive employee fields."""
        return bool(actor.role in ("owner", "manager") or actor.is_hr_head)

    # ------------------------------------------------------------------
    # Directory List via `public.pub_employees` (Prompt 4)
    # ------------------------------------------------------------------

    @staticmethod
    async def list_employees(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        search: str | None = None,
        department_id: UUID | None = None,
        status_filter: str | None = None,
        employment_type: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> EmployeeListResponse:
        """
        Queries `public.pub_employees` joined with `departments` and manager name
        so encrypted CNIC/bank columns are NEVER selected in list responses.
        """
        t_id = UUID(str(tenant_id))
        safe_page = max(1, page)
        safe_page_size = max(1, min(100, page_size))
        offset = (safe_page - 1) * safe_page_size

        where_clauses = ["e.tenant_id = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": t_id}

        clean_search = (search or "").strip()
        if clean_search:
            where_clauses.append(
                "(e.full_name ILIKE :search_pattern "
                "OR e.email ILIKE :search_pattern "
                "OR e.job_title ILIKE :search_pattern "
                "OR COALESCE(e.employee_code, '') ILIKE :search_pattern)"
            )
            params["search_pattern"] = f"%{clean_search}%"

        if department_id is not None:
            where_clauses.append("e.department_id = :department_id")
            params["department_id"] = department_id

        if status_filter:
            where_clauses.append("e.status = :status_filter")
            params["status_filter"] = status_filter.strip().lower()

        if employment_type:
            where_clauses.append("e.employment_type = :employment_type")
            params["employment_type"] = employment_type.strip().lower()

        where_sql = " AND ".join(where_clauses)

        count_sql = text(f"SELECT COUNT(*) FROM public.pub_employees e WHERE {where_sql}")
        total = int((await session.execute(count_sql, params)).scalar() or 0)

        query_sql = text(
            f"""
            SELECT
                e.id,
                e.tenant_id,
                e.user_id,
                e.employee_code,
                e.full_name,
                e.email,
                e.phone,
                e.department_id,
                d.name AS department_name,
                e.manager_id,
                m.full_name AS manager_name,
                e.job_title,
                e.employment_type,
                e.join_date,
                e.leaving_date,
                e.status,
                e.created_at,
                e.updated_at
            FROM public.pub_employees e
            LEFT JOIN public.departments d
              ON d.id = e.department_id AND d.tenant_id = e.tenant_id
            LEFT JOIN public.pub_employees m
              ON m.id = e.manager_id AND m.tenant_id = e.tenant_id
            WHERE {where_sql}
            ORDER BY e.full_name ASC, e.created_at ASC
            LIMIT :limit OFFSET :offset
            """
        )
        rows = (
            (
                await session.execute(
                    query_sql,
                    {**params, "limit": safe_page_size, "offset": offset},
                )
            )
            .mappings()
            .all()
        )

        items = [EmployeeListItem(**dict(row)) for row in rows]
        total_pages = math.ceil(total / safe_page_size) if total > 0 else 0

        return EmployeeListResponse(
            items=items,
            total=total,
            page=safe_page,
            page_size=safe_page_size,
            total_pages=total_pages,
        )

    # ------------------------------------------------------------------
    # Role-Based Employee Profile (Prompt 4)
    # ------------------------------------------------------------------

    @classmethod
    async def get_employee_profile(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        employee_id: UUID,
        actor: AuthenticatedUserContext,
        log_sensitive_access: bool = True,
    ) -> EmployeeProfileResponse:
        """
        Returns full employee profile with strict role-based encrypted PII decryption.
        Logs `employee.sensitive_viewed` to `audit_logs` ONLY when `can_view_sensitive` is True
        and at least one encrypted PII field (`cnic` or `bank_account`) was decrypted.
        """
        from app.models.department import Department
        from app.models.employee import Employee

        t_id = UUID(str(tenant_id))
        stmt = select(Employee).where(
            Employee.tenant_id == t_id,
            Employee.id == employee_id,
        )
        emp = (await session.execute(stmt)).scalar_one_or_none()
        if not emp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee '{employee_id}' not found.",
            )

        department_name: str | None = None
        if emp.department_id:
            dept_stmt = select(Department.name).where(
                Department.tenant_id == t_id,
                Department.id == emp.department_id,
            )
            department_name = (await session.execute(dept_stmt)).scalar_one_or_none()

        manager_name: str | None = None
        manager_email: str | None = None
        if emp.manager_id:
            mgr_stmt = select(Employee.full_name, Employee.email).where(
                Employee.tenant_id == t_id,
                Employee.id == emp.manager_id,
            )
            mgr_row = (await session.execute(mgr_stmt)).first()
            if mgr_row:
                manager_name, manager_email = mgr_row[0], mgr_row[1]

        can_view = cls.can_view_sensitive_pii(actor, emp.user_id)
        can_edit = cls.can_edit_sensitive_pii(actor)

        cnic_plain: str | None = None
        bank_plain: str | None = None
        cnic_masked: str | None = None
        bank_masked: str | None = None

        if can_view:
            decrypted_fields: list[str] = []
            if emp.cnic_encrypted:
                cnic_plain = cls.decrypt_cnic(t_id, emp.id, emp.cnic_encrypted)
                cnic_masked = cls.mask_cnic(cnic_plain)
                decrypted_fields.append("cnic")
            if emp.bank_account_encrypted:
                bank_plain = cls.decrypt_bank_account(t_id, emp.id, emp.bank_account_encrypted)
                bank_masked = cls.mask_bank_account(bank_plain)
                decrypted_fields.append("bank_account")

            if log_sensitive_access and decrypted_fields:
                is_self_view = bool(
                    emp.user_id is not None and str(emp.user_id) == str(actor.user_id)
                )
                await write_audit(
                    session,
                    action="employee.sensitive_viewed",
                    target_type="employee",
                    target_id=emp.id,
                    details={
                        "fields_accessed": decrypted_fields,
                        "is_self_view": is_self_view,
                        "actor_role": actor.role,
                    },
                    actor_id=actor.user_id,
                    tenant_id=t_id,
                )

        return EmployeeProfileResponse(
            id=emp.id,
            tenant_id=emp.tenant_id,
            user_id=emp.user_id,
            employee_code=emp.employee_code,
            full_name=emp.full_name,
            email=emp.email,
            phone=emp.phone,
            department_id=emp.department_id,
            department_name=department_name,
            manager_id=emp.manager_id,
            manager_name=manager_name,
            manager_email=manager_email,
            job_title=emp.job_title,
            employment_type=emp.employment_type,
            join_date=emp.join_date,
            leaving_date=emp.leaving_date,
            status=emp.status,
            created_at=emp.created_at,
            updated_at=emp.updated_at,
            can_view_sensitive=can_view,
            can_edit_sensitive=can_edit,
            cnic=cnic_plain,
            bank_account=bank_plain,
            cnic_masked=cnic_masked,
            bank_account_masked=bank_masked,
        )

    # ------------------------------------------------------------------
    # Create & Update Employee with Cycle Detection & PII Guard (Prompt 4)
    # ------------------------------------------------------------------

    @staticmethod
    async def _validate_manager_assignment(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        new_manager_id: UUID,
    ) -> None:
        """
        Ensures `new_manager_id` exists in the tenant and does not create
        a self-parenting or multi-hop cycle in the org tree.
        """
        from app.models.employee import Employee

        if new_manager_id == employee_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ORG_CHART_CYCLE: An employee cannot be their own manager.",
            )

        rows = (
            await session.execute(
                select(Employee.id, Employee.manager_id).where(Employee.tenant_id == tenant_id)
            )
        ).all()
        parent_map: dict[UUID, UUID | None] = {row[0]: row[1] for row in rows}

        if new_manager_id not in parent_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Manager '{new_manager_id}' does not exist in this tenant.",
            )

        visited: set[UUID] = {employee_id}
        curr: UUID | None = new_manager_id
        while curr is not None:
            if curr in visited:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="ORG_CHART_CYCLE: Assigning this manager would create a circular reporting hierarchy.",
                )
            visited.add(curr)
            curr = parent_map.get(curr)

    @classmethod
    async def create_employee(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        payload: EmployeeCreateRequest,
        actor: AuthenticatedUserContext,
    ) -> EmployeeProfileResponse:
        """
        Creates a new employee record with AES-256-GCM encrypted PII (restricted to
        owner / hr_head / payroll_admin), enqueues Sheets sync, and writes audit_logs.
        """
        from app.models.department import Department
        from app.models.employee import Employee

        if not cls.can_manage_employees(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Owner, Manager, or HR Head privileges required.",
            )

        has_sensitive_payload = bool(
            (payload.cnic and payload.cnic.strip())
            or (payload.bank_account and payload.bank_account.strip())
        )
        if has_sensitive_payload and not cls.can_edit_sensitive_pii(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="PII_EDIT_FORBIDDEN: Only Owner, HR Head, or Payroll Admin may set CNIC or bank account.",
            )

        t_id = UUID(str(tenant_id))
        email_norm = payload.email.strip().lower()
        if not EMAIL_REGEX.match(email_norm):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid email format: '{payload.email}'.",
            )

        dup = (
            await session.execute(
                select(Employee.id).where(
                    Employee.tenant_id == t_id,
                    func.lower(Employee.email) == email_norm,
                )
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"DUPLICATE_EMPLOYEE_EMAIL: Employee with email '{email_norm}' already exists.",
            )

        dept_name = "Unassigned"
        if payload.department_id is not None:
            dept = (
                await session.execute(
                    select(Department).where(
                        Department.tenant_id == t_id,
                        Department.id == payload.department_id,
                    )
                )
            ).scalar_one_or_none()
            if not dept:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Department '{payload.department_id}' does not exist in this tenant.",
                )
            dept_name = dept.name

        if payload.manager_id is not None:
            mgr_exists = (
                await session.execute(
                    select(Employee.id).where(
                        Employee.tenant_id == t_id,
                        Employee.id == payload.manager_id,
                    )
                )
            ).scalar_one_or_none()
            if not mgr_exists:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Manager '{payload.manager_id}' does not exist in this tenant.",
                )

        cnic_clean = payload.cnic.strip() if payload.cnic and payload.cnic.strip() else None
        if cnic_clean and not CNIC_REGEX.match(cnic_clean):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid CNIC format. Expected 13-digit Pakistani CNIC (XXXXX-XXXXXXX-X or 13 digits).",
            )
        bank_clean = (
            payload.bank_account.strip()
            if payload.bank_account and payload.bank_account.strip()
            else None
        )

        # Pre-generate UUID so 4-field canonical AAD binds to the exact persisted employee_id
        emp_id = uuid.uuid4()
        cnic_enc = cls.encrypt_cnic(t_id, emp_id, cnic_clean) if cnic_clean else None
        bank_enc = cls.encrypt_bank_account(t_id, emp_id, bank_clean) if bank_clean else None

        emp_code = (
            payload.employee_code.strip()
            if payload.employee_code and payload.employee_code.strip()
            else f"EMP-{emp_id.hex[:6].upper()}"
        )

        emp = Employee(
            id=emp_id,
            tenant_id=t_id,
            user_id=payload.user_id,
            employee_code=emp_code,
            full_name=payload.full_name.strip(),
            email=email_norm,
            phone=payload.phone.strip() if payload.phone and payload.phone.strip() else None,
            department_id=payload.department_id,
            manager_id=payload.manager_id,
            job_title=payload.job_title.strip(),
            employment_type=payload.employment_type,
            join_date=payload.join_date,
            leaving_date=payload.leaving_date,
            status=payload.status,
            cnic_encrypted=cnic_enc,
            bank_account_encrypted=bank_enc,
        )
        session.add(emp)
        await session.flush()

        await initialize_employee_leave_balances(
            session,
            tenant_id=t_id,
            employee_id=emp.id,
            actor_id=actor.user_id,
        )

        await SheetsSyncService.enqueue_sync(
            session=session,
            tenant_id=t_id,
            entity_type="employee",
            entity_id=str(emp.id),
            action="create",
            payload={
                "employee_id": emp.employee_code or str(emp.id)[:8].upper(),
                "name": emp.full_name,
                "email": emp.email,
                "department": dept_name,
                "role": emp.job_title,
                "status": emp.status,
            },
        )

        await write_audit(
            session,
            action="employee.created",
            target_type="employee",
            target_id=emp.id,
            details={
                "employee_code": emp.employee_code,
                "email": emp.email,
                "department_id": str(emp.department_id) if emp.department_id else None,
                "has_cnic": bool(cnic_enc),
                "has_bank_account": bool(bank_enc),
            },
            actor_id=actor.user_id,
            tenant_id=t_id,
        )

        return await cls.get_employee_profile(
            session=session,
            tenant_id=t_id,
            employee_id=emp.id,
            actor=actor,
            log_sensitive_access=False,
        )

    @classmethod
    async def update_employee(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        employee_id: UUID,
        payload: EmployeeUpdateRequest,
        actor: AuthenticatedUserContext,
    ) -> EmployeeProfileResponse:
        """
        Updates an employee record. Enforces role-based PII modification guards,
        prevents self-parenting or circular manager hierarchies, enqueues Sheets sync,
        and writes immutable `audit_logs`.
        """
        from app.models.department import Department
        from app.models.employee import Employee

        if not cls.can_manage_employees(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Owner, Manager, or HR Head privileges required.",
            )

        fields_set = payload.model_fields_set
        if (
            "cnic" in fields_set or "bank_account" in fields_set
        ) and not cls.can_edit_sensitive_pii(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="PII_EDIT_FORBIDDEN: Only Owner, HR Head, or Payroll Admin may modify CNIC or bank account.",
            )

        t_id = UUID(str(tenant_id))
        emp = (
            await session.execute(
                select(Employee).where(
                    Employee.tenant_id == t_id,
                    Employee.id == employee_id,
                )
            )
        ).scalar_one_or_none()
        if not emp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee '{employee_id}' not found.",
            )

        updated_fields: list[str] = []

        if "full_name" in fields_set and payload.full_name is not None:
            emp.full_name = payload.full_name.strip()
            updated_fields.append("full_name")

        if "email" in fields_set and payload.email is not None:
            email_norm = payload.email.strip().lower()
            if not EMAIL_REGEX.match(email_norm):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid email format: '{payload.email}'.",
                )
            if email_norm != emp.email.lower():
                dup = (
                    await session.execute(
                        select(Employee.id).where(
                            Employee.tenant_id == t_id,
                            func.lower(Employee.email) == email_norm,
                            Employee.id != emp.id,
                        )
                    )
                ).scalar_one_or_none()
                if dup:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"DUPLICATE_EMPLOYEE_EMAIL: Employee with email '{email_norm}' already exists.",
                    )
                emp.email = email_norm
                updated_fields.append("email")

        if "phone" in fields_set:
            emp.phone = payload.phone.strip() if payload.phone and payload.phone.strip() else None
            updated_fields.append("phone")

        if "employee_code" in fields_set:
            emp.employee_code = (
                payload.employee_code.strip()
                if payload.employee_code and payload.employee_code.strip()
                else None
            )
            updated_fields.append("employee_code")

        if "user_id" in fields_set:
            emp.user_id = payload.user_id
            updated_fields.append("user_id")

        if "department_id" in fields_set:
            if payload.department_id is not None:
                dept_exists = (
                    await session.execute(
                        select(Department.id).where(
                            Department.tenant_id == t_id,
                            Department.id == payload.department_id,
                        )
                    )
                ).scalar_one_or_none()
                if not dept_exists:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Department '{payload.department_id}' does not exist in this tenant.",
                    )
            emp.department_id = payload.department_id
            updated_fields.append("department_id")

        if "manager_id" in fields_set:
            if payload.manager_id is not None:
                await cls._validate_manager_assignment(
                    session=session,
                    tenant_id=t_id,
                    employee_id=emp.id,
                    new_manager_id=payload.manager_id,
                )
            emp.manager_id = payload.manager_id
            updated_fields.append("manager_id")

        if "job_title" in fields_set and payload.job_title is not None:
            emp.job_title = payload.job_title.strip()
            updated_fields.append("job_title")

        if "employment_type" in fields_set and payload.employment_type is not None:
            emp.employment_type = payload.employment_type
            updated_fields.append("employment_type")

        if "join_date" in fields_set and payload.join_date is not None:
            emp.join_date = payload.join_date
            updated_fields.append("join_date")

        if "leaving_date" in fields_set:
            emp.leaving_date = payload.leaving_date
            updated_fields.append("leaving_date")

        if "status" in fields_set and payload.status is not None:
            emp.status = payload.status
            updated_fields.append("status")

        if "cnic" in fields_set:
            cnic_clean = payload.cnic.strip() if payload.cnic and payload.cnic.strip() else None
            if cnic_clean and not CNIC_REGEX.match(cnic_clean):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid CNIC format. Expected 13-digit Pakistani CNIC (XXXXX-XXXXXXX-X or 13 digits).",
                )
            emp.cnic_encrypted = cls.encrypt_cnic(t_id, emp.id, cnic_clean) if cnic_clean else None
            updated_fields.append("cnic")

        if "bank_account" in fields_set:
            bank_clean = (
                payload.bank_account.strip()
                if payload.bank_account and payload.bank_account.strip()
                else None
            )
            emp.bank_account_encrypted = (
                cls.encrypt_bank_account(t_id, emp.id, bank_clean) if bank_clean else None
            )
            updated_fields.append("bank_account")

        await session.flush()

        dept_name = "Unassigned"
        if emp.department_id:
            dept_name = (
                await session.execute(
                    select(Department.name).where(
                        Department.tenant_id == t_id,
                        Department.id == emp.department_id,
                    )
                )
            ).scalar_one_or_none() or "Unassigned"

        await SheetsSyncService.enqueue_sync(
            session=session,
            tenant_id=t_id,
            entity_type="employee",
            entity_id=str(emp.id),
            action="update",
            payload={
                "employee_id": emp.employee_code or str(emp.id)[:8].upper(),
                "name": emp.full_name,
                "email": emp.email,
                "department": dept_name,
                "role": emp.job_title,
                "status": emp.status,
            },
        )

        await write_audit(
            session,
            action="employee.updated",
            target_type="employee",
            target_id=emp.id,
            details={"updated_fields": sorted(set(updated_fields))},
            actor_id=actor.user_id,
            tenant_id=t_id,
        )

        return await cls.get_employee_profile(
            session=session,
            tenant_id=t_id,
            employee_id=emp.id,
            actor=actor,
            log_sensitive_access=False,
        )

    # ------------------------------------------------------------------
    # Cycle-Safe Org Chart via `public.pub_employees` (Prompt 4)
    # ------------------------------------------------------------------

    @staticmethod
    async def get_org_chart(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
    ) -> OrgChartResponse:
        """
        Queries `public.pub_employees` and builds a cycle-safe hierarchical org tree
        (`roots[]` + `unassigned[]` for any orphans or broken references).
        """
        t_id = UUID(str(tenant_id))
        query_sql = text(
            """
            SELECT
                e.id,
                e.employee_code,
                e.full_name,
                e.job_title,
                d.name AS department_name,
                e.status,
                e.email,
                e.manager_id
            FROM public.pub_employees e
            LEFT JOIN public.departments d
              ON d.id = e.department_id AND d.tenant_id = e.tenant_id
            WHERE e.tenant_id = :tenant_id
            ORDER BY e.full_name ASC, e.id ASC
            """
        )
        rows = (await session.execute(query_sql, {"tenant_id": t_id})).mappings().all()

        nodes_by_id: dict[UUID, OrgChartNode] = {}
        children_map: dict[UUID, list[UUID]] = {}
        root_ids: list[UUID] = []
        orphan_ids: list[UUID] = []

        for row in rows:
            emp_id = row["id"]
            nodes_by_id[emp_id] = OrgChartNode(
                id=emp_id,
                employee_code=row["employee_code"],
                full_name=row["full_name"],
                job_title=row["job_title"],
                department_name=row["department_name"],
                status=row["status"],
                email=row["email"],
                manager_id=row["manager_id"],
                direct_reports_count=0,
                children=[],
            )
            children_map[emp_id] = []

        for emp_id, node in nodes_by_id.items():
            if node.manager_id is None:
                root_ids.append(emp_id)
            elif node.manager_id in nodes_by_id and node.manager_id != emp_id:
                children_map[node.manager_id].append(emp_id)
            else:
                orphan_ids.append(emp_id)

        visited: set[UUID] = set()

        def _build_subtree(node_id: UUID) -> OrgChartNode:
            visited.add(node_id)
            node = nodes_by_id[node_id]
            child_nodes: list[OrgChartNode] = []
            for child_id in children_map.get(node_id, []):
                if child_id not in visited:
                    child_nodes.append(_build_subtree(child_id))
            node.children = child_nodes
            node.direct_reports_count = len(child_nodes)
            return node

        roots = [_build_subtree(rid) for rid in root_ids if rid not in visited]

        unassigned: list[OrgChartNode] = []
        for oid in orphan_ids:
            if oid not in visited:
                unassigned.append(_build_subtree(oid))

        # Safety net: any remaining unvisited nodes (e.g. legacy cycles) go to unassigned[]
        for remaining_id in nodes_by_id:
            if remaining_id not in visited:
                unassigned.append(_build_subtree(remaining_id))

        return OrgChartResponse(
            total_employees=len(nodes_by_id),
            roots=roots,
            unassigned=unassigned,
        )
