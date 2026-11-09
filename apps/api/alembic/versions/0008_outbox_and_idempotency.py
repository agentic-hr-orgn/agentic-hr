"""create outbox_events and idempotency_keys tables with rls policies

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09 15:00:00.000000

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
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Create 'outbox_events' table
    # ---------------------------------------------------------
    op.create_table(
        "outbox_events",
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
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column(
            "event_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_error",
            sa.Text(),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'failed')",
            name="chk_outbox_events_status",
        ),
    )

    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
    op.create_index(
        "ix_outbox_events_status_next_attempt",
        "outbox_events",
        ["status", "next_attempt_at"],
    )
    op.create_index("ix_outbox_events_created_at", "outbox_events", ["created_at"])
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])

    # Enable RLS & create tenant policies
    enable_rls("public.outbox_events")
    create_tenant_rls_policies("public.outbox_events")

    # Role Grants
    op.execute("GRANT ALL ON TABLE public.outbox_events TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON TABLE public.outbox_events FROM agentic_hr_app;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.outbox_events TO agentic_hr_app;"
    )

    # View Alias
    op.execute("CREATE OR REPLACE VIEW public.outbox_event AS SELECT * FROM public.outbox_events;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.outbox_event TO agentic_hr_app;")
    op.execute("GRANT ALL ON public.outbox_event TO agentic_hr_migrator;")

    # ---------------------------------------------------------
    # 2. Create 'idempotency_keys' table
    # ---------------------------------------------------------
    op.create_table(
        "idempotency_keys",
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
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="started",
            nullable=False,
        ),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column(
            "response_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "locked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("tenant_id", "key", name="uq_idempotency_keys_tenant_key"),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'failed')",
            name="chk_idempotency_keys_status",
        ),
    )

    op.create_index("ix_idempotency_keys_tenant_id", "idempotency_keys", ["tenant_id"])
    op.create_index(
        "ix_idempotency_keys_tenant_operation",
        "idempotency_keys",
        ["tenant_id", "operation"],
    )
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])

    # Enable RLS & create tenant policies
    enable_rls("public.idempotency_keys")
    create_tenant_rls_policies("public.idempotency_keys")

    # Role Grants
    op.execute("GRANT ALL ON TABLE public.idempotency_keys TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON TABLE public.idempotency_keys FROM agentic_hr_app;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.idempotency_keys TO agentic_hr_app;"
    )

    # View Alias
    op.execute(
        "CREATE OR REPLACE VIEW public.idempotency_key AS SELECT * FROM public.idempotency_keys;"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.idempotency_key TO agentic_hr_app;")
    op.execute("GRANT ALL ON public.idempotency_key TO agentic_hr_migrator;")


def downgrade() -> None:
    # ---------------------------------------------------------
    # 1. Downgrade 'idempotency_keys'
    # ---------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS public.idempotency_key;")
    drop_tenant_rls_policies("public.idempotency_keys")
    disable_rls("public.idempotency_keys")

    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_tenant_operation", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_tenant_id", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")

    # ---------------------------------------------------------
    # 2. Downgrade 'outbox_events'
    # ---------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS public.outbox_event;")
    drop_tenant_rls_policies("public.outbox_events")
    disable_rls("public.outbox_events")

    op.drop_index("ix_outbox_events_event_type", table_name="outbox_events")
    op.drop_index("ix_outbox_events_created_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status_next_attempt", table_name="outbox_events")
    op.drop_index("ix_outbox_events_tenant_id", table_name="outbox_events")
    op.drop_table("outbox_events")
