"""create multi-tenant approval_revisions table and expand approval statuses

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07 22:50:00.000000

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
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Expand 'approvals' status constraint to include 'changes_requested'
    # ---------------------------------------------------------
    op.drop_constraint("chk_approvals_status", "approvals", type_="check")
    op.create_check_constraint(
        "chk_approvals_status",
        "approvals",
        "status IN ('pending', 'changes_requested', 'approved', 'rejected')",
    )

    # ---------------------------------------------------------
    # 2. Add 'current_revision' and 'resubmitted_at' columns to 'approvals'
    # ---------------------------------------------------------
    op.add_column(
        "approvals",
        sa.Column(
            "current_revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Current active revision/round number (starts at 1)",
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "resubmitted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when request was last resubmitted after changes requested",
        ),
    )

    # ---------------------------------------------------------
    # 3. Create 'approval_revisions' table for append-only round history
    # ---------------------------------------------------------
    op.create_table(
        "approval_revisions",
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
            "approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approvals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "revision_number",
            sa.Integer(),
            nullable=False,
            comment="Monotonically increasing revision number (1, 2, 3...)",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Snapshot of the domain payload submitted for this revision round",
        ),
        sa.Column(
            "submitted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "decision",
            sa.String(length=32),
            nullable=True,
            comment="Decision committed on this revision: pending, changes_requested, approved, rejected",
        ),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decision_reason",
            sa.Text(),
            nullable=True,
            comment="Correction instructions (when changes_requested) or decision rationale",
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('pending', 'changes_requested', 'approved', 'rejected')",
            name="chk_approval_revisions_decision",
        ),
        sa.UniqueConstraint(
            "approval_id",
            "revision_number",
            name="uq_approval_revision_round",
        ),
    )

    # ---------------------------------------------------------
    # 4. Create Indexes
    # ---------------------------------------------------------
    op.create_index("ix_approval_revisions_tenant_id", "approval_revisions", ["tenant_id"])
    op.create_index("ix_approval_revisions_approval_id", "approval_revisions", ["approval_id"])
    op.create_index("ix_approval_revisions_submitted_at", "approval_revisions", ["submitted_at"])

    # ---------------------------------------------------------
    # 5. Data Backfill: Populate initial revision 1 for all existing approvals
    # ---------------------------------------------------------
    op.execute(
        """
        INSERT INTO approval_revisions (
            id, tenant_id, approval_id, revision_number, payload,
            submitted_by, submitted_at, decision, decided_by,
            decision_reason, decided_at, created_at
        )
        SELECT 
            gen_random_uuid(), tenant_id, id, 1, payload,
            requester_id, created_at, 
            CASE WHEN status = 'pending' THEN NULL ELSE status END,
            decided_by, decision_reason, decided_at, created_at
        FROM approvals;
        """
    )

    # ---------------------------------------------------------
    # 6. Row-Level Security (RLS)
    # ---------------------------------------------------------
    enable_rls("public.approval_revisions")
    create_tenant_rls_policies("public.approval_revisions")

    # ---------------------------------------------------------
    # 7. Least-Privilege Role Grants (agentic_hr_app has NO DELETE)
    # ---------------------------------------------------------
    op.execute("GRANT ALL ON TABLE public.approval_revisions TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON TABLE public.approval_revisions FROM agentic_hr_app;")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE public.approval_revisions TO agentic_hr_app;")

    # ---------------------------------------------------------
    # 8. View Alias ('approval_revision')
    # ---------------------------------------------------------
    op.execute(
        "CREATE OR REPLACE VIEW public.approval_revision AS SELECT * FROM public.approval_revisions;"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON public.approval_revision TO agentic_hr_app;")
    op.execute("GRANT ALL ON public.approval_revision TO agentic_hr_migrator;")


def downgrade() -> None:
    # 1. Drop View Alias
    op.execute("DROP VIEW IF EXISTS public.approval_revision;")

    # 2. Drop RLS Policies & Disable RLS
    drop_tenant_rls_policies("public.approval_revisions")
    disable_rls("public.approval_revisions")

    # 3. Drop Indexes & Table
    op.drop_index("ix_approval_revisions_submitted_at", table_name="approval_revisions")
    op.drop_index("ix_approval_revisions_approval_id", table_name="approval_revisions")
    op.drop_index("ix_approval_revisions_tenant_id", table_name="approval_revisions")
    op.drop_table("approval_revisions")

    # 4. Drop added columns from 'approvals'
    op.drop_column("approvals", "resubmitted_at")
    op.drop_column("approvals", "current_revision")

    # 5. Revert check constraint on 'approvals'
    op.drop_constraint("chk_approvals_status", "approvals", type_="check")
    op.create_check_constraint(
        "chk_approvals_status",
        "approvals",
        "status IN ('pending', 'approved', 'rejected')",
    )
