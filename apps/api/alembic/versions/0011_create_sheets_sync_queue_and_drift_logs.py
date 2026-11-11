"""create sheets_sync_queue and sheets_drift_logs tables with rls policies

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-10 14:30:00.000000

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
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Create 'sheets_sync_queue' table
    # ---------------------------------------------------------
    op.create_table(
        "sheets_sync_queue",
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
            "credential_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connector_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("spreadsheet_id", sa.String(length=255), nullable=False),
        sa.Column(
            "sheet_name",
            sa.String(length=100),
            server_default="Employees",
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.String(length=50),
            server_default="postgres_to_sheets",
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
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
        sa.CheckConstraint(
            "direction IN ('postgres_to_sheets', 'sheets_to_postgres_import')",
            name="chk_sheets_sync_queue_direction",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="chk_sheets_sync_queue_status",
        ),
    )

    op.create_index(
        "ix_sheets_sync_queue_tenant_id",
        "sheets_sync_queue",
        ["tenant_id"],
    )
    op.create_index(
        "ix_sheets_sync_queue_status_scheduled_at",
        "sheets_sync_queue",
        ["status", "scheduled_at"],
    )
    op.create_index(
        "ix_sheets_sync_queue_entity",
        "sheets_sync_queue",
        ["tenant_id", "entity_type", "entity_id"],
    )

    # Enable RLS & create tenant policies
    enable_rls("public.sheets_sync_queue")
    create_tenant_rls_policies("public.sheets_sync_queue")

    # ---------------------------------------------------------
    # 2. Create 'sheets_drift_logs' table
    # ---------------------------------------------------------
    op.create_table(
        "sheets_drift_logs",
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
        sa.Column("spreadsheet_id", sa.String(length=255), nullable=False),
        sa.Column(
            "sheet_name",
            sa.String(length=100),
            server_default="Employees",
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=False),
        sa.Column("postgres_value", sa.Text(), nullable=True),
        sa.Column("sheets_value", sa.Text(), nullable=True),
        sa.Column(
            "resolution",
            sa.String(length=50),
            server_default="postgres_overwrote_sheets",
            nullable=False,
        ),
        sa.Column(
            "resolved_by",
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
        sa.CheckConstraint(
            "resolution IN ('postgres_overwrote_sheets', 'flagged_for_human_review', 'ignored')",
            name="chk_sheets_drift_logs_resolution",
        ),
    )

    op.create_index(
        "ix_sheets_drift_logs_tenant_id",
        "sheets_drift_logs",
        ["tenant_id"],
    )
    op.create_index(
        "ix_sheets_drift_logs_entity",
        "sheets_drift_logs",
        ["tenant_id", "entity_type", "entity_id"],
    )

    # Enable RLS & create tenant policies
    enable_rls("public.sheets_drift_logs")
    create_tenant_rls_policies("public.sheets_drift_logs")

    # ---------------------------------------------------------
    # 3. Create view aliases for singular/plural mapping
    # ---------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'sheets_sync_queue_item' AND schemaname = 'public') THEN
                CREATE VIEW public.sheets_sync_queue_item AS SELECT * FROM public.sheets_sync_queue;
                ALTER VIEW public.sheets_sync_queue_item OWNER TO postgres;
                GRANT SELECT, INSERT, UPDATE, DELETE ON public.sheets_sync_queue_item TO agentic_hr_app;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'sheets_drift_log' AND schemaname = 'public') THEN
                CREATE VIEW public.sheets_drift_log AS SELECT * FROM public.sheets_drift_logs;
                ALTER VIEW public.sheets_drift_log OWNER TO postgres;
                GRANT SELECT, INSERT, UPDATE, DELETE ON public.sheets_drift_log TO agentic_hr_app;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.sheets_drift_log CASCADE;")
    op.execute("DROP VIEW IF EXISTS public.sheets_sync_queue_item CASCADE;")

    drop_tenant_rls_policies("public.sheets_drift_logs")
    disable_rls("public.sheets_drift_logs")
    op.drop_table("sheets_drift_logs")

    drop_tenant_rls_policies("public.sheets_sync_queue")
    disable_rls("public.sheets_sync_queue")
    op.drop_table("sheets_sync_queue")
