"""stage 4: departments, employees, documents, onboarding state, notifications, and secure views

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-20 23:10:00.000000

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
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE4_TABLES: tuple[str, ...] = (
    "departments",
    "employees",
    "employee_documents",
    "policy_documents",
    "onboarding_state",
    "notifications",
)


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1a. Create 'departments' table
    # ---------------------------------------------------------
    op.create_table(
        "departments",
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
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("head_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
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
    )

    op.create_index("ix_departments_tenant_id", "departments", ["tenant_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_departments_tenant_lower_name "
        "ON public.departments (tenant_id, lower(name));"
    )

    # ---------------------------------------------------------
    # 1b. Create 'employees' table
    # ---------------------------------------------------------
    op.create_table(
        "employees",
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
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("employee_code", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "manager_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("job_title", sa.String(length=150), nullable=False),
        sa.Column(
            "employment_type",
            sa.String(length=32),
            server_default="full_time",
            nullable=False,
        ),
        sa.Column("join_date", sa.Date(), nullable=False),
        sa.Column("leaving_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
        sa.Column("cnic_encrypted", sa.Text(), nullable=True),
        sa.Column("bank_account_encrypted", sa.Text(), nullable=True),
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
            "employment_type IN ('full_time', 'part_time', 'contract', 'intern')",
            name="ck_employees_employment_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'invited', 'on_leave', 'terminated', 'resigned')",
            name="ck_employees_status",
        ),
    )

    op.create_index("ix_employees_tenant_id", "employees", ["tenant_id"])
    op.create_index(
        "ix_employees_tenant_department",
        "employees",
        ["tenant_id", "department_id"],
    )
    op.create_index(
        "ix_employees_tenant_manager",
        "employees",
        ["tenant_id", "manager_id"],
    )
    op.create_index(
        "ix_employees_tenant_status",
        "employees",
        ["tenant_id", "status"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_employees_tenant_lower_email "
        "ON public.employees (tenant_id, lower(email));"
    )

    # Link departments.head_employee_id -> employees.id ON DELETE SET NULL
    op.create_foreign_key(
        "fk_departments_head_employee",
        "departments",
        "employees",
        ["head_employee_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ---------------------------------------------------------
    # 1c. Create 'employee_documents' table
    # ---------------------------------------------------------
    op.create_table(
        "employee_documents",
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
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "storage_provider",
            sa.String(length=32),
            server_default="local",
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("web_view_link", sa.String(length=512), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            "document_type IN ('cnic', 'contract', 'certificate', 'other')",
            name="ck_employee_documents_document_type",
        ),
        sa.CheckConstraint(
            "storage_provider IN ('local', 'google_drive')",
            name="ck_employee_documents_storage_provider",
        ),
    )

    op.create_index("ix_employee_documents_tenant_id", "employee_documents", ["tenant_id"])
    op.create_index(
        "ix_employee_documents_tenant_employee",
        "employee_documents",
        ["tenant_id", "employee_id"],
    )
    op.create_index(
        "ix_employee_documents_tenant_expiry",
        "employee_documents",
        ["tenant_id", "expiry_date"],
    )

    # ---------------------------------------------------------
    # 1d. Create 'policy_documents' table
    # ---------------------------------------------------------
    op.create_table(
        "policy_documents",
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
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "storage_provider",
            sa.String(length=32),
            server_default="local",
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "ingestion_status",
            sa.String(length=32),
            server_default="queued_stub",
            nullable=False,
        ),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
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
    )

    op.create_index("ix_policy_documents_tenant_id", "policy_documents", ["tenant_id"])

    # ---------------------------------------------------------
    # 1e. Create 'onboarding_state' table
    # ---------------------------------------------------------
    op.create_table(
        "onboarding_state",
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
            unique=True,
        ),
        sa.Column(
            "current_step",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "completed_steps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("business_type", sa.String(length=128), nullable=True),
        sa.Column(
            "structure_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "is_live",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
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
    )

    op.create_index("ix_onboarding_state_tenant_id", "onboarding_state", ["tenant_id"])

    # ---------------------------------------------------------
    # 1f. Create 'notifications' table
    # ---------------------------------------------------------
    op.create_table(
        "notifications",
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
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column(
            "severity",
            sa.String(length=32),
            server_default="info",
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column(
            "is_read",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('document_expiry', 'onboarding', 'approval', 'system')",
            name="ck_notifications_category",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_notifications_severity",
        ),
    )

    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])
    op.create_index(
        "ix_notifications_tenant_unread",
        "notifications",
        ["tenant_id", "is_read", "created_at"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_notifications_tenant_dedupe_key "
        "ON public.notifications (tenant_id, dedupe_key) "
        "WHERE dedupe_key IS NOT NULL;"
    )

    # ---------------------------------------------------------
    # 2. Enable Forced RLS, Policies, Ownership & Grants on all 6 tables
    # ---------------------------------------------------------
    for tbl in STAGE4_TABLES:
        qualified = f"public.{tbl}"
        enable_rls(qualified)
        create_tenant_rls_policies(qualified)
        op.execute(f"ALTER TABLE {qualified} OWNER TO agentic_hr_migrator;")
        op.execute(f"GRANT ALL ON TABLE {qualified} TO agentic_hr_migrator;")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {qualified} TO agentic_hr_app;")
        op.execute(f"REVOKE TRUNCATE ON TABLE {qualified} FROM agentic_hr_app;")

    # ---------------------------------------------------------
    # 3. Create Secure Read Views (CRITICAL Pass 1 Invariant — NO RLS BYPASS)
    # ---------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW public.pub_employees
        WITH (security_invoker = true, security_barrier = true) AS
        SELECT
            id,
            tenant_id,
            user_id,
            employee_code,
            full_name,
            email,
            phone,
            department_id,
            manager_id,
            job_title,
            employment_type,
            join_date,
            leaving_date,
            status,
            created_at,
            updated_at
        FROM public.employees;
        """
    )
    op.execute("ALTER VIEW public.pub_employees OWNER TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON public.pub_employees FROM PUBLIC;")
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.pub_employees FROM agentic_hr_app;"
    )
    op.execute("GRANT SELECT ON public.pub_employees TO agentic_hr_app;")

    op.execute(
        """
        CREATE OR REPLACE VIEW public.pub_documents_expiring
        WITH (security_invoker = true, security_barrier = true) AS
        SELECT
            d.id AS document_id,
            d.tenant_id,
            d.employee_id,
            e.full_name AS employee_name,
            e.email AS employee_email,
            e.status AS employee_status,
            d.document_type,
            d.title,
            d.expiry_date,
            (
                d.expiry_date - (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Karachi')::date
            )::integer AS days_until_expiry
        FROM public.employee_documents d
        JOIN public.employees e
          ON d.employee_id = e.id AND d.tenant_id = e.tenant_id
        WHERE d.expiry_date IS NOT NULL;
        """
    )
    op.execute("ALTER VIEW public.pub_documents_expiring OWNER TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON public.pub_documents_expiring FROM PUBLIC;")
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE "
        "ON public.pub_documents_expiring FROM agentic_hr_app;"
    )
    op.execute("GRANT SELECT ON public.pub_documents_expiring TO agentic_hr_app;")


def downgrade() -> None:
    # 1. Drop secure read views
    op.execute("DROP VIEW IF EXISTS public.pub_documents_expiring CASCADE;")
    op.execute("DROP VIEW IF EXISTS public.pub_employees CASCADE;")

    # 2. Drop cross-table FK between departments and employees first
    op.drop_constraint("fk_departments_head_employee", "departments", type_="foreignkey")

    # 3. Drop RLS policies, disable RLS, and drop tables in reverse dependency order
    for tbl in reversed(STAGE4_TABLES):
        qualified = f"public.{tbl}"
        drop_tenant_rls_policies(qualified)
        disable_rls(qualified)
        op.drop_table(tbl)
