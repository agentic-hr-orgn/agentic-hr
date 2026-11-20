"""
Employee Model for Agentic HR Core (Stage 4).
Complies with ADR 002 (Multi-Tenancy with forced PostgreSQL RLS) and Stage 2 Phase 9
AES-256-GCM Envelope Encryption for sensitive PII (CNIC and bank account).
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.encryption import KeyProvider
from app.db.base import Base


class Employee(Base):
    """
    Tenant-scoped employee directory & encrypted record keeper entity.
    Non-sensitive columns are projected via `public.pub_employees` (security_invoker=true).
    """

    __tablename__ = "employees"
    __table_args__ = (
        Index("ix_employees_tenant_id", "tenant_id"),
        Index("ix_employees_tenant_department", "tenant_id", "department_id"),
        Index("ix_employees_tenant_manager", "tenant_id", "manager_id"),
        Index("ix_employees_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "employment_type IN ('full_time', 'part_time', 'contract', 'intern')",
            name="ck_employees_employment_type",
        ),
        CheckConstraint(
            "status IN ('active', 'invited', 'on_leave', 'terminated', 'resigned')",
            name="ck_employees_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant scope for RLS isolation",
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    employee_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
    )
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_title: Mapped[str] = mapped_column(String(150), nullable=False)
    employment_type: Mapped[str] = mapped_column(
        String(32),
        default="full_time",
        server_default="full_time",
        nullable=False,
    )
    join_date: Mapped[date] = mapped_column(Date, nullable=False)
    leaving_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        server_default="active",
        nullable=False,
    )
    cnic_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="AES-256-GCM envelope encrypted CNIC bound to 4-field AAD",
    )
    bank_account_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="AES-256-GCM envelope encrypted bank account / IBAN bound to 4-field AAD",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def _ensure_identity_for_aad(self) -> tuple[uuid.UUID, uuid.UUID]:
        if self.id is None:
            self.id = uuid.uuid4()
        if self.tenant_id is None:
            raise ValueError("Employee.tenant_id must be set before encrypting or decrypting PII.")
        return self.tenant_id, self.id

    def set_cnic(
        self,
        plaintext_cnic: str | None,
        key_id: str | None = None,
        key_provider: KeyProvider | None = None,
    ) -> None:
        """Encrypts and stores CNIC using canonical 4-field AAD."""
        if plaintext_cnic is None or not str(plaintext_cnic).strip():
            self.cnic_encrypted = None
            return
        from app.services.employee_service import EmployeeService

        tenant_id, emp_id = self._ensure_identity_for_aad()
        self.cnic_encrypted = EmployeeService.encrypt_cnic(
            tenant_id=tenant_id,
            employee_id=emp_id,
            plaintext_cnic=plaintext_cnic,
            key_id=key_id,
            key_provider=key_provider,
        )

    def get_cnic(self, key_provider: KeyProvider | None = None) -> str | None:
        """Decrypts and returns plaintext CNIC if present."""
        if not self.cnic_encrypted:
            return None
        from app.services.employee_service import EmployeeService

        tenant_id, emp_id = self._ensure_identity_for_aad()
        return EmployeeService.decrypt_cnic(
            tenant_id=tenant_id,
            employee_id=emp_id,
            encrypted_cnic=self.cnic_encrypted,
            key_provider=key_provider,
        )

    def get_masked_cnic(self, key_provider: KeyProvider | None = None) -> str | None:
        """Decrypts and masks CNIC as '*****-*******-1'."""
        from app.services.employee_service import EmployeeService

        return EmployeeService.mask_cnic(self.get_cnic(key_provider=key_provider))

    def set_bank_account(
        self,
        plaintext_bank_account: str | None,
        key_id: str | None = None,
        key_provider: KeyProvider | None = None,
    ) -> None:
        """Encrypts and stores bank account / IBAN using canonical 4-field AAD."""
        if plaintext_bank_account is None or not str(plaintext_bank_account).strip():
            self.bank_account_encrypted = None
            return
        from app.services.employee_service import EmployeeService

        tenant_id, emp_id = self._ensure_identity_for_aad()
        self.bank_account_encrypted = EmployeeService.encrypt_bank_account(
            tenant_id=tenant_id,
            employee_id=emp_id,
            plaintext_bank_account=plaintext_bank_account,
            key_id=key_id,
            key_provider=key_provider,
        )

    def get_bank_account(self, key_provider: KeyProvider | None = None) -> str | None:
        """Decrypts and returns plaintext bank account / IBAN if present."""
        if not self.bank_account_encrypted:
            return None
        from app.services.employee_service import EmployeeService

        tenant_id, emp_id = self._ensure_identity_for_aad()
        return EmployeeService.decrypt_bank_account(
            tenant_id=tenant_id,
            employee_id=emp_id,
            encrypted_bank_account=self.bank_account_encrypted,
            key_provider=key_provider,
        )

    def get_masked_bank_account(self, key_provider: KeyProvider | None = None) -> str | None:
        """Decrypts and masks bank account as '****1234'."""
        from app.services.employee_service import EmployeeService

        return EmployeeService.mask_bank_account(self.get_bank_account(key_provider=key_provider))
