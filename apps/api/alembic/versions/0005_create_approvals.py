"""create multi-tenant approvals table with rls and least privilege permissions

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-07 20:30:00.000000

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
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Create 'approvals' table
    # ---------------------------------------------------------
    op.create_table(
        "approvals",
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
        sa.Column("approval_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "requester_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approver_role", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="chk_approvals_status",
        ),
        sa.CheckConstraint(
            "approver_role IN ('owner', 'manager', 'hr_head', 'payroll_admin')",
            name="chk_approvals_approver_role",
        ),
    )

    # ---------------------------------------------------------
    # 2. Create Indexes
    # ---------------------------------------------------------
    op.create_index("ix_approvals_tenant_id", "approvals", ["tenant_id"])
    op.create_index("ix_approvals_tenant_status", "approvals", ["tenant_id", "status"])
    op.create_index("ix_approvals_requester_id", "approvals", ["requester_id"])
    op.create_index("ix_approvals_created_at", "approvals", ["created_at"])

    # ---------------------------------------------------------
    # 3. Row-Level Security (RLS)
    # ---------------------------------------------------------
    enable_rls("public.approvals")
    create_tenant_rls_policies("public.approvals")

    # ---------------------------------------------------------
    # 4. Least-Privilege Role Grants (agentic_hr_app has NO DELETE)
    # ---------------------------------------------------------
    op.execute("GRANT ALL ON TABLE public.approvals TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON TABLE public.approvals FROM agentic_hr_app;")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE public.approvals TO agentic_hr_app;")

    # ---------------------------------------------------------
    # 5. View Alias ('approval')
    # ---------------------------------------------------------
    op.execute("CREATE OR REPLACE VIEW public.approval AS SELECT * FROM public.approvals;")
    op.execute("GRANT SELECT, INSERT, UPDATE ON public.approval TO agentic_hr_app;")
    op.execute("GRANT ALL ON public.approval TO agentic_hr_migrator;")


def downgrade() -> None:
    # 1. Drop View Alias
    op.execute("DROP VIEW IF EXISTS public.approval;")

    # 2. Drop RLS Policies & Disable RLS
    drop_tenant_rls_policies("public.approvals")
    disable_rls("public.approvals")

    # 3. Drop Indexes & Table
    op.drop_index("ix_approvals_created_at", table_name="approvals")
    op.drop_index("ix_approvals_requester_id", table_name="approvals")
    op.drop_index("ix_approvals_tenant_status", table_name="approvals")
    op.drop_index("ix_approvals_tenant_id", table_name="approvals")
    op.drop_table("approvals")
