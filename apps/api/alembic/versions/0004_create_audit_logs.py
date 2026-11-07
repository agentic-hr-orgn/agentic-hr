"""create immutable multi-tenant audit logs table and triggers

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07 19:00:00.000000

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
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Create 'audit_logs' table
    # ---------------------------------------------------------
    op.create_table(
        "audit_logs",
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
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
    )

    # ---------------------------------------------------------
    # 2. Create Indexes
    # ---------------------------------------------------------
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_tenant_action", "audit_logs", ["tenant_id", "action"])

    # ---------------------------------------------------------
    # 3. Immutability Trigger (Prohibits UPDATE & DELETE)
    # ---------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION public.prevent_audit_log_modification()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Audit log entries are immutable and append-only. UPDATE and DELETE operations are strictly prohibited.'
            USING ERRCODE = '55000';
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_audit_logs_immutable
    BEFORE UPDATE OR DELETE ON public.audit_logs
    FOR EACH ROW
    EXECUTE FUNCTION public.prevent_audit_log_modification();
    """)

    # ---------------------------------------------------------
    # 4. Row-Level Security (RLS)
    # ---------------------------------------------------------
    enable_rls("public.audit_logs")
    create_tenant_rls_policies("public.audit_logs")

    # ---------------------------------------------------------
    # 5. Least-Privilege Role Grants
    # ---------------------------------------------------------
    op.execute("GRANT ALL ON TABLE public.audit_logs TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON TABLE public.audit_logs FROM agentic_hr_app;")
    op.execute("GRANT SELECT, INSERT ON TABLE public.audit_logs TO agentic_hr_app;")

    # ---------------------------------------------------------
    # 6. View Alias ('audit_log')
    # ---------------------------------------------------------
    op.execute("CREATE OR REPLACE VIEW public.audit_log AS SELECT * FROM public.audit_logs;")
    op.execute("GRANT SELECT ON public.audit_log TO agentic_hr_app;")
    op.execute("GRANT ALL ON public.audit_log TO agentic_hr_migrator;")


def downgrade() -> None:
    # 1. Drop View Alias
    op.execute("DROP VIEW IF EXISTS public.audit_log;")

    # 2. Drop RLS Policies & Disable RLS
    drop_tenant_rls_policies("public.audit_logs")
    disable_rls("public.audit_logs")

    # 3. Drop Immutability Trigger & Function
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON public.audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS public.prevent_audit_log_modification();")

    # 4. Drop Indexes & Table
    op.drop_index("ix_audit_logs_tenant_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_timestamp", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_id", table_name="audit_logs")
    op.drop_table("audit_logs")
