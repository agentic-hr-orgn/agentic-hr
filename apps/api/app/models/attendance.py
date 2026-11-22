"""
Attendance & Month-Lock Governance ORM Models for Agentic HR Core (Stage 6).
All tables enforce Forced Row-Level Security (ADR 002).
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Attendance(Base):
    """Per-employee, per-date attendance record."""

    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "employee_id",
            "attendance_date",
            name="uq_attendance_tenant_emp_date",
        ),
        Index("ix_attendance_tenant_id", "tenant_id"),
        Index("ix_attendance_tenant_date", "tenant_id", "attendance_date"),
        Index("ix_attendance_tenant_emp_date", "tenant_id", "employee_id", "attendance_date"),
        CheckConstraint(
            "status IN ('PRESENT', 'ABSENT', 'HALF_DAY', 'ON_LEAVE', 'WFH', 'HOLIDAY', 'WEEKEND')",
            name="ck_attendance_status",
        ),
        CheckConstraint(
            "source IN ('check_in', 'manager_grid', 'excel_import', 'auto_fill', 'leave_approval', 'post_lock_edit')",
            name="ck_attendance_source",
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
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    locked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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


class AttendanceMonthLock(Base):
    """Per-tenant monthly attendance lock record for payroll finalization."""

    __tablename__ = "attendance_month_locks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "year_month",
            name="uq_attendance_month_locks_tenant_ym",
        ),
        Index("ix_attendance_month_locks_tenant_id", "tenant_id"),
        CheckConstraint(
            "year_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            name="ck_attendance_month_locks_year_month_format",
        ),
        CheckConstraint("employee_count >= 0", name="ck_attendance_month_locks_employee_count"),
        CheckConstraint("record_count >= 0", name="ck_attendance_month_locks_record_count"),
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
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)
    locked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    employee_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    record_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
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
