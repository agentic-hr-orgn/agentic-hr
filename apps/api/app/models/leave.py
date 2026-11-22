"""
Leave Management ORM Models for Agentic HR Core (Stage 6).
Covers leave_types, holidays, work_week, leave_balances, leave_ledger, and leave_requests.
All tables enforce Forced Row-Level Security (ADR 002).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LeaveType(Base):
    """Tenant-scoped leave category and policy rules."""

    __tablename__ = "leave_types"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_leave_types_tenant_code"),
        Index("ix_leave_types_tenant_id", "tenant_id"),
        Index("ix_leave_types_tenant_active", "tenant_id", "is_active"),
        CheckConstraint(
            "code = lower(btrim(code)) AND length(btrim(code)) > 0",
            name="ck_leave_types_code_lowercase",
        ),
        CheckConstraint("annual_allowance_days >= 0", name="ck_leave_types_annual_allowance"),
        CheckConstraint(
            "accrual_mode IN ('upfront', 'monthly')",
            name="ck_leave_types_accrual_mode",
        ),
        CheckConstraint(
            "carry_forward_cap_days >= 0",
            name="ck_leave_types_carry_forward_cap",
        ),
        CheckConstraint(
            "max_consecutive_days IS NULL OR max_consecutive_days >= 1",
            name="ck_leave_types_max_consecutive_days",
        ),
        CheckConstraint("min_notice_days >= 0", name="ck_leave_types_min_notice_days"),
        CheckConstraint(
            "requires_document_after_days IS NULL OR requires_document_after_days >= 1",
            name="ck_leave_types_requires_doc_after",
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
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    annual_allowance_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    accrual_mode: Mapped[str] = mapped_column(
        String(16),
        default="upfront",
        server_default="upfront",
        nullable=False,
    )
    carry_forward_cap_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    max_consecutive_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_notice_days: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    allow_during_probation: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    requires_document_after_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allow_half_day: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    is_paid: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
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


class Holiday(Base):
    """Tenant-scoped public and optional holidays."""

    __tablename__ = "holidays"
    __table_args__ = (
        UniqueConstraint("tenant_id", "holiday_date", name="uq_holidays_tenant_date"),
        Index("ix_holidays_tenant_id", "tenant_id"),
        Index("ix_holidays_tenant_date", "tenant_id", "holiday_date"),
        CheckConstraint("source IN ('default_pk', 'custom')", name="ck_holidays_source"),
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
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_optional: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(24),
        default="custom",
        server_default="custom",
        nullable=False,
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


class WorkWeek(Base):
    """Per-tenant work-week, probation, team coverage, and check-in configuration."""

    __tablename__ = "work_week"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_work_week_tenant_id"),
        Index("ix_work_week_tenant_id", "tenant_id"),
        CheckConstraint(
            "jsonb_typeof(weekend_days) = 'array'",
            name="ck_work_week_weekend_days_array",
        ),
        CheckConstraint(
            "probation_months >= 0 AND probation_months <= 24",
            name="ck_work_week_probation_months",
        ),
        CheckConstraint(
            "max_team_absent_pct >= 0 AND max_team_absent_pct <= 100",
            name="ck_work_week_max_team_absent_pct",
        ),
        CheckConstraint("min_team_present >= 0", name="ck_work_week_min_team_present"),
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
    )
    weekend_days: Mapped[list[str]] = mapped_column(
        JSONB,
        default=lambda: ["Saturday", "Sunday"],
        server_default=text('\'["Saturday", "Sunday"]\'::jsonb'),
        nullable=False,
    )
    probation_months: Mapped[int] = mapped_column(
        Integer,
        default=3,
        server_default=text("3"),
        nullable=False,
    )
    max_team_absent_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("30.00"),
        server_default=text("30.00"),
        nullable=False,
    )
    min_team_present: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )
    backup_approver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    check_in_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="Asia/Karachi",
        server_default="Asia/Karachi",
        nullable=False,
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


class LeaveBalance(Base):
    """Per-employee, per-leave-type, per-year leave balance snapshot."""

    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "employee_id",
            "leave_type_id",
            "year",
            name="uq_leave_balances_tenant_emp_type_year",
        ),
        Index("ix_leave_balances_tenant_id", "tenant_id"),
        Index("ix_leave_balances_tenant_emp_year", "tenant_id", "employee_id", "year"),
        CheckConstraint("year >= 2000 AND year <= 2100", name="ck_leave_balances_year"),
        CheckConstraint("allocated_days >= 0", name="ck_leave_balances_allocated_non_neg"),
        CheckConstraint(
            "carried_forward_days >= 0",
            name="ck_leave_balances_carried_forward_non_neg",
        ),
        CheckConstraint("reserved_days >= 0", name="ck_leave_balances_reserved_non_neg"),
        CheckConstraint("used_days >= 0", name="ck_leave_balances_used_non_neg"),
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
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    carried_forward_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    reserved_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    used_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
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


class LeaveLedger(Base):
    """Immutable append-only double-entry leave ledger."""

    __tablename__ = "leave_ledger"
    __table_args__ = (
        Index("ix_leave_ledger_tenant_id", "tenant_id"),
        Index("ix_leave_ledger_tenant_emp_year", "tenant_id", "employee_id", "year", "created_at"),
        Index("ix_leave_ledger_balance_id", "balance_id"),
        CheckConstraint("year >= 2000 AND year <= 2100", name="ck_leave_ledger_year"),
        CheckConstraint(
            "entry_type IN ('accrual', 'carry_forward', 'reserve', 'release', 'deduct', 'reversal', 'adjustment')",
            name="ck_leave_ledger_entry_type",
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
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    balance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_balances.id", ondelete="CASCADE"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    delta_allocated: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    delta_reserved: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    delta_used: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    reference_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class LeaveRequest(Base):
    """Employee leave request with policy evaluation, escalation, and Temporal workflow tracking."""

    __tablename__ = "leave_requests"
    __table_args__ = (
        Index("ix_leave_requests_tenant_id", "tenant_id"),
        Index(
            "ix_leave_requests_tenant_emp_dates",
            "tenant_id",
            "employee_id",
            "start_date",
            "end_date",
        ),
        Index("ix_leave_requests_tenant_status", "tenant_id", "status"),
        Index("ix_leave_requests_tenant_approver", "tenant_id", "approver_user_id", "status"),
        CheckConstraint("end_date >= start_date", name="ck_leave_requests_date_order"),
        CheckConstraint("working_days > 0", name="ck_leave_requests_working_days_pos"),
        CheckConstraint(
            "half_day_period IS NULL OR half_day_period IN ('morning', 'afternoon')",
            name="ck_leave_requests_half_day_period",
        ),
        CheckConstraint(
            "(NOT is_half_day) OR (start_date = end_date AND half_day_period IN ('morning', 'afternoon'))",
            name="ck_leave_requests_half_day_consistency",
        ),
        CheckConstraint(
            "policy_check_status IN ('OK', 'VIOLATION', 'BLOCKED')",
            name="ck_leave_requests_policy_check_status",
        ),
        CheckConstraint(
            "status IN ('pending', 'pending_hr_exception', 'approved', 'rejected', 'cancelled', 'expired')",
            name="ck_leave_requests_status",
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
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_half_day: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    half_day_period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    working_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employee_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_check_status: Mapped[str] = mapped_column(
        String(16),
        default="OK",
        server_default="OK",
        nullable=False,
    )
    policy_check_reasons: Mapped[list[Any]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default="pending",
        nullable=False,
    )
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    escalated_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    hr_exception_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approvals.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheets_row_synced: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
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
