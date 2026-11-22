"""stage 6 prompt 1: leave & attendance schema, forced RLS, immutable ledger & secure views

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-22 14:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import (
    create_tenant_rls_policies,
    disable_rls,
    drop_tenant_rls_policies,
    enable_rls,
)

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE6_TABLES: tuple[str, ...] = (
    "leave_types",
    "holidays",
    "work_week",
    "leave_balances",
    "leave_ledger",
    "leave_requests",
    "attendance",
    "attendance_month_locks",
)


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Create public.leave_types
    # ---------------------------------------------------------
    op.create_table(
        "leave_types",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "annual_allowance_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "accrual_mode",
            sa.String(length=16),
            server_default="upfront",
            nullable=False,
        ),
        sa.Column(
            "carry_forward_cap_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column("max_consecutive_days", sa.Integer(), nullable=True),
        sa.Column(
            "min_notice_days",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "allow_during_probation",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("requires_document_after_days", sa.Integer(), nullable=True),
        sa.Column(
            "allow_half_day",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_paid",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "code", name="uq_leave_types_tenant_code"),
        sa.CheckConstraint(
            "code = lower(btrim(code)) AND length(btrim(code)) > 0",
            name="ck_leave_types_code_lowercase",
        ),
        sa.CheckConstraint(
            "annual_allowance_days >= 0",
            name="ck_leave_types_annual_allowance",
        ),
        sa.CheckConstraint(
            "accrual_mode IN ('upfront', 'monthly')",
            name="ck_leave_types_accrual_mode",
        ),
        sa.CheckConstraint(
            "carry_forward_cap_days >= 0",
            name="ck_leave_types_carry_forward_cap",
        ),
        sa.CheckConstraint(
            "max_consecutive_days IS NULL OR max_consecutive_days >= 1",
            name="ck_leave_types_max_consecutive_days",
        ),
        sa.CheckConstraint(
            "min_notice_days >= 0",
            name="ck_leave_types_min_notice_days",
        ),
        sa.CheckConstraint(
            "requires_document_after_days IS NULL OR requires_document_after_days >= 1",
            name="ck_leave_types_requires_doc_after",
        ),
    )
    op.create_index("ix_leave_types_tenant_id", "leave_types", ["tenant_id"])
    op.create_index(
        "ix_leave_types_tenant_active",
        "leave_types",
        ["tenant_id", "is_active"],
    )

    # ---------------------------------------------------------
    # 2. Create public.holidays
    # ---------------------------------------------------------
    op.create_table(
        "holidays",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "is_optional",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=24),
            server_default="custom",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "holiday_date", name="uq_holidays_tenant_date"),
        sa.CheckConstraint(
            "source IN ('default_pk', 'custom')",
            name="ck_holidays_source",
        ),
    )
    op.create_index("ix_holidays_tenant_id", "holidays", ["tenant_id"])
    op.create_index("ix_holidays_tenant_date", "holidays", ["tenant_id", "holiday_date"])

    # ---------------------------------------------------------
    # 3. Create public.work_week (1 row per tenant)
    # ---------------------------------------------------------
    op.create_table(
        "work_week",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "weekend_days",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text('\'["Saturday", "Sunday"]\'::jsonb'),
            nullable=False,
        ),
        sa.Column(
            "probation_months",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column(
            "max_team_absent_pct",
            sa.Numeric(precision=5, scale=2),
            server_default=sa.text("30.00"),
            nullable=False,
        ),
        sa.Column(
            "min_team_present",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "backup_approver_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "check_in_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Asia/Karachi",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", name="uq_work_week_tenant_id"),
        sa.CheckConstraint(
            "jsonb_typeof(weekend_days) = 'array'",
            name="ck_work_week_weekend_days_array",
        ),
        sa.CheckConstraint(
            "probation_months >= 0 AND probation_months <= 24",
            name="ck_work_week_probation_months",
        ),
        sa.CheckConstraint(
            "max_team_absent_pct >= 0 AND max_team_absent_pct <= 100",
            name="ck_work_week_max_team_absent_pct",
        ),
        sa.CheckConstraint(
            "min_team_present >= 0",
            name="ck_work_week_min_team_present",
        ),
    )
    op.create_index("ix_work_week_tenant_id", "work_week", ["tenant_id"])

    # ---------------------------------------------------------
    # 4. Create public.leave_balances
    # ---------------------------------------------------------
    op.create_table(
        "leave_balances",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "leave_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leave_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "allocated_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "carried_forward_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "reserved_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "used_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_id",
            "leave_type_id",
            "year",
            name="uq_leave_balances_tenant_emp_type_year",
        ),
        sa.CheckConstraint(
            "year >= 2000 AND year <= 2100",
            name="ck_leave_balances_year",
        ),
        sa.CheckConstraint(
            "allocated_days >= 0",
            name="ck_leave_balances_allocated_non_neg",
        ),
        sa.CheckConstraint(
            "carried_forward_days >= 0",
            name="ck_leave_balances_carried_forward_non_neg",
        ),
        sa.CheckConstraint(
            "reserved_days >= 0",
            name="ck_leave_balances_reserved_non_neg",
        ),
        sa.CheckConstraint(
            "used_days >= 0",
            name="ck_leave_balances_used_non_neg",
        ),
    )
    op.create_index("ix_leave_balances_tenant_id", "leave_balances", ["tenant_id"])
    op.create_index(
        "ix_leave_balances_tenant_emp_year",
        "leave_balances",
        ["tenant_id", "employee_id", "year"],
    )

    # ---------------------------------------------------------
    # 5. Create public.leave_ledger (immutable append-only)
    # ---------------------------------------------------------
    op.create_table(
        "leave_ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "leave_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leave_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "balance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leave_balances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(length=32), nullable=False),
        sa.Column(
            "delta_allocated",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "delta_reserved",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column(
            "delta_used",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0.00"),
            nullable=False,
        ),
        sa.Column("reference_type", sa.String(length=64), nullable=True),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "year >= 2000 AND year <= 2100",
            name="ck_leave_ledger_year",
        ),
        sa.CheckConstraint(
            "entry_type IN ('accrual', 'carry_forward', 'reserve', 'release', 'deduct', 'reversal', 'adjustment')",
            name="ck_leave_ledger_entry_type",
        ),
    )
    op.create_index("ix_leave_ledger_tenant_id", "leave_ledger", ["tenant_id"])
    op.create_index(
        "ix_leave_ledger_tenant_emp_year",
        "leave_ledger",
        ["tenant_id", "employee_id", "year", "created_at"],
    )
    op.create_index("ix_leave_ledger_balance_id", "leave_ledger", ["balance_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_leave_ledger_tenant_idempotency_key "
        "ON public.leave_ledger (tenant_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL;"
    )

    # ---------------------------------------------------------
    # 6. Create public.leave_requests
    # ---------------------------------------------------------
    op.create_table(
        "leave_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "leave_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leave_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "is_half_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("half_day_period", sa.String(length=16), nullable=True),
        sa.Column("working_days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employee_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "policy_check_status",
            sa.String(length=16),
            server_default="OK",
            nullable=False,
        ),
        sa.Column(
            "policy_check_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "approver_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "escalated_to_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "hr_exception_approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approvals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("workflow_id", sa.String(length=128), nullable=True),
        sa.Column("calendar_event_id", sa.String(length=255), nullable=True),
        sa.Column(
            "sheets_row_synced",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "end_date >= start_date",
            name="ck_leave_requests_date_order",
        ),
        sa.CheckConstraint(
            "working_days > 0",
            name="ck_leave_requests_working_days_pos",
        ),
        sa.CheckConstraint(
            "half_day_period IS NULL OR half_day_period IN ('morning', 'afternoon')",
            name="ck_leave_requests_half_day_period",
        ),
        sa.CheckConstraint(
            "(NOT is_half_day) OR (start_date = end_date AND half_day_period IN ('morning', 'afternoon'))",
            name="ck_leave_requests_half_day_consistency",
        ),
        sa.CheckConstraint(
            "policy_check_status IN ('OK', 'VIOLATION', 'BLOCKED')",
            name="ck_leave_requests_policy_check_status",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'pending_hr_exception', 'approved', 'rejected', 'cancelled', 'expired')",
            name="ck_leave_requests_status",
        ),
    )
    op.create_index("ix_leave_requests_tenant_id", "leave_requests", ["tenant_id"])
    op.create_index(
        "ix_leave_requests_tenant_emp_dates",
        "leave_requests",
        ["tenant_id", "employee_id", "start_date", "end_date"],
    )
    op.create_index(
        "ix_leave_requests_tenant_status",
        "leave_requests",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_leave_requests_tenant_approver",
        "leave_requests",
        ["tenant_id", "approver_user_id", "status"],
    )

    # ---------------------------------------------------------
    # 7. Create public.attendance
    # ---------------------------------------------------------
    op.create_table(
        "attendance",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "leave_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leave_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "locked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_id",
            "attendance_date",
            name="uq_attendance_tenant_emp_date",
        ),
        sa.CheckConstraint(
            "status IN ('PRESENT', 'ABSENT', 'HALF_DAY', 'ON_LEAVE', 'WFH', 'HOLIDAY', 'WEEKEND')",
            name="ck_attendance_status",
        ),
        sa.CheckConstraint(
            "source IN ('check_in', 'manager_grid', 'excel_import', 'auto_fill', 'leave_approval', 'post_lock_edit')",
            name="ck_attendance_source",
        ),
    )
    op.create_index("ix_attendance_tenant_id", "attendance", ["tenant_id"])
    op.create_index(
        "ix_attendance_tenant_date",
        "attendance",
        ["tenant_id", "attendance_date"],
    )
    op.create_index(
        "ix_attendance_tenant_emp_date",
        "attendance",
        ["tenant_id", "employee_id", "attendance_date"],
    )

    # ---------------------------------------------------------
    # 8. Create public.attendance_month_locks
    # ---------------------------------------------------------
    op.create_table(
        "attendance_month_locks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year_month", sa.String(length=7), nullable=False),
        sa.Column(
            "locked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "locked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "employee_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "record_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "year_month",
            name="uq_attendance_month_locks_tenant_ym",
        ),
        sa.CheckConstraint(
            "year_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            name="ck_attendance_month_locks_year_month_format",
        ),
        sa.CheckConstraint(
            "employee_count >= 0",
            name="ck_attendance_month_locks_employee_count",
        ),
        sa.CheckConstraint(
            "record_count >= 0",
            name="ck_attendance_month_locks_record_count",
        ),
    )
    op.create_index(
        "ix_attendance_month_locks_tenant_id",
        "attendance_month_locks",
        ["tenant_id"],
    )

    # ---------------------------------------------------------
    # 9. Enforce Forced RLS, Ownership & Grants on all 8 tables
    # ---------------------------------------------------------
    for tbl in STAGE6_TABLES:
        qualified = f"public.{tbl}"
        enable_rls(qualified)
        create_tenant_rls_policies(qualified)
        op.execute(f"ALTER TABLE {qualified} OWNER TO agentic_hr_migrator;")
        op.execute(f"GRANT ALL ON TABLE {qualified} TO agentic_hr_migrator;")
        if tbl == "leave_ledger":
            op.execute(f"REVOKE ALL ON TABLE {qualified} FROM agentic_hr_app;")
            op.execute(f"GRANT SELECT, INSERT ON TABLE {qualified} TO agentic_hr_app;")
            op.execute(f"REVOKE TRUNCATE ON TABLE {qualified} FROM agentic_hr_migrator;")
        else:
            op.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {qualified} TO agentic_hr_app;"
            )
            op.execute(f"REVOKE TRUNCATE ON TABLE {qualified} FROM agentic_hr_app;")

    # ---------------------------------------------------------
    # 10. Append-only immutability triggers on public.leave_ledger
    #     - Unconditionally blocks UPDATE and TRUNCATE.
    #     - Blocks DELETE when executed by runtime role `agentic_hr_app`,
    #       while allowing migrator/admin FK cascades in test teardown.
    # ---------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.prevent_leave_ledger_modification()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_user <> 'agentic_hr_app' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'Leave ledger entries are immutable and append-only. UPDATE, TRUNCATE, and runtime DELETE operations are strictly prohibited.'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_leave_ledger_immutable
        BEFORE UPDATE OR DELETE ON public.leave_ledger
        FOR EACH ROW
        EXECUTE FUNCTION public.prevent_leave_ledger_modification();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_leave_ledger_no_truncate
        BEFORE TRUNCATE ON public.leave_ledger
        FOR EACH STATEMENT
        EXECUTE FUNCTION public.prevent_leave_ledger_modification();
        """
    )

    # ---------------------------------------------------------
    # 11. Create Secure Read Views (security_invoker + security_barrier)
    # ---------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW public.pub_attendance
        WITH (security_invoker = true, security_barrier = true) AS
        SELECT
            a.tenant_id,
            a.employee_id,
            e.full_name AS employee_name,
            e.email AS employee_email,
            d.name AS department_name,
            a.attendance_date,
            a.status,
            a.source,
            a.locked
        FROM public.attendance a
        JOIN public.employees e
          ON a.employee_id = e.id AND a.tenant_id = e.tenant_id
        LEFT JOIN public.departments d
          ON e.department_id = d.id AND e.tenant_id = d.tenant_id;
        """
    )
    op.execute("ALTER VIEW public.pub_attendance OWNER TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON public.pub_attendance FROM PUBLIC;")
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.pub_attendance FROM agentic_hr_app;"
    )
    op.execute("GRANT SELECT ON public.pub_attendance TO agentic_hr_app;")

    op.execute(
        """
        CREATE OR REPLACE VIEW public.pub_leave
        WITH (security_invoker = true, security_barrier = true) AS
        SELECT
            lr.tenant_id,
            lr.id AS request_id,
            lr.employee_id,
            e.full_name AS employee_name,
            lt.code AS leave_type_code,
            lt.name AS leave_type_name,
            lr.start_date,
            lr.end_date,
            lr.is_half_day,
            lr.working_days,
            lr.status,
            lr.policy_check_status,
            lr.created_at
        FROM public.leave_requests lr
        JOIN public.employees e
          ON lr.employee_id = e.id AND lr.tenant_id = e.tenant_id
        JOIN public.leave_types lt
          ON lr.leave_type_id = lt.id AND lr.tenant_id = lt.tenant_id;
        """
    )
    op.execute("ALTER VIEW public.pub_leave OWNER TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON public.pub_leave FROM PUBLIC;")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.pub_leave FROM agentic_hr_app;")
    op.execute("GRANT SELECT ON public.pub_leave TO agentic_hr_app;")


def downgrade() -> None:
    # 1. Drop secure views
    op.execute("DROP VIEW IF EXISTS public.pub_leave CASCADE;")
    op.execute("DROP VIEW IF EXISTS public.pub_attendance CASCADE;")

    # 2. Drop leave_ledger triggers & function
    op.execute("DROP TRIGGER IF EXISTS trg_leave_ledger_no_truncate ON public.leave_ledger;")
    op.execute("DROP TRIGGER IF EXISTS trg_leave_ledger_immutable ON public.leave_ledger;")
    op.execute("DROP FUNCTION IF EXISTS public.prevent_leave_ledger_modification();")

    # 3. Drop RLS policies, disable RLS, and drop tables in reverse dependency order
    for tbl in reversed(STAGE6_TABLES):
        qualified = f"public.{tbl}"
        drop_tenant_rls_policies(qualified)
        disable_rls(qualified)
        op.drop_table(tbl)
