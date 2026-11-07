"""add workflow_id column to approvals table

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07 23:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Add 'workflow_id' column to 'approvals'
    # ---------------------------------------------------------
    op.add_column(
        "approvals",
        sa.Column(
            "workflow_id",
            sa.String(length=128),
            nullable=True,
            comment="Optional Temporal workflow execution ID associated with this approval gate",
        ),
    )

    # ---------------------------------------------------------
    # 2. Create index on 'workflow_id' for fast reverse lookup
    # ---------------------------------------------------------
    op.create_index(
        "ix_approvals_workflow_id",
        "approvals",
        ["workflow_id"],
        unique=False,
    )


def downgrade() -> None:
    # ---------------------------------------------------------
    # 1. Drop index 'ix_approvals_workflow_id'
    # ---------------------------------------------------------
    op.drop_index("ix_approvals_workflow_id", table_name="approvals")

    # ---------------------------------------------------------
    # 2. Drop 'workflow_id' column
    # ---------------------------------------------------------
    op.drop_column("approvals", "workflow_id")
