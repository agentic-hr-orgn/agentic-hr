"""create connector_credentials and sent_messages tables with rls policies

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-10 11:30:00.000000

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
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Create 'connector_credentials' table
    # ---------------------------------------------------------
    op.create_table(
        "connector_credentials",
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
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("account_email", sa.String(length=255), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="connected",
            nullable=False,
        ),
        sa.Column(
            "is_read_only",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "jobs_paused",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            "provider",
            "account_email",
            name="uq_connector_credentials_tenant_provider_email",
        ),
        sa.CheckConstraint(
            "status IN ('connected', 'expired', 'revoked', 'needs_reconnect')",
            name="chk_connector_credentials_status",
        ),
    )

    op.create_index(
        "ix_connector_credentials_tenant_id",
        "connector_credentials",
        ["tenant_id"],
    )
    op.create_index(
        "ix_connector_credentials_tenant_provider",
        "connector_credentials",
        ["tenant_id", "provider"],
    )
    op.create_index(
        "ix_connector_credentials_status",
        "connector_credentials",
        ["status"],
    )

    # Enable RLS & create tenant policies
    enable_rls("public.connector_credentials")
    create_tenant_rls_policies("public.connector_credentials")

    # Role Grants
    op.execute("GRANT ALL ON TABLE public.connector_credentials TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON TABLE public.connector_credentials FROM agentic_hr_app;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.connector_credentials TO agentic_hr_app;"
    )

    # View Alias
    op.execute(
        "CREATE OR REPLACE VIEW public.connector_credential AS SELECT * FROM public.connector_credentials;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON public.connector_credential TO agentic_hr_app;"
    )
    op.execute("GRANT ALL ON public.connector_credential TO agentic_hr_migrator;")

    # ---------------------------------------------------------
    # 2. Create 'sent_messages' table
    # ---------------------------------------------------------
    op.create_table(
        "sent_messages",
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
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("template_name", sa.String(length=128), nullable=True),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="sent",
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            "status IN ('sent', 'bounced', 'failed')",
            name="chk_sent_messages_status",
        ),
    )

    op.create_index("ix_sent_messages_tenant_id", "sent_messages", ["tenant_id"])
    op.create_index(
        "ix_sent_messages_tenant_idempotency",
        "sent_messages",
        ["tenant_id", "idempotency_key"],
    )
    op.create_index(
        "ix_sent_messages_tenant_provider_msg_id",
        "sent_messages",
        ["tenant_id", "provider_message_id"],
    )
    op.create_index("ix_sent_messages_recipient_email", "sent_messages", ["recipient_email"])
    op.create_index("ix_sent_messages_status", "sent_messages", ["status"])

    # Enable RLS & create tenant policies
    enable_rls("public.sent_messages")
    create_tenant_rls_policies("public.sent_messages")

    # Role Grants
    op.execute("GRANT ALL ON TABLE public.sent_messages TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON TABLE public.sent_messages FROM agentic_hr_app;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.sent_messages TO agentic_hr_app;"
    )

    # View Alias
    op.execute("CREATE OR REPLACE VIEW public.sent_message AS SELECT * FROM public.sent_messages;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.sent_message TO agentic_hr_app;")
    op.execute("GRANT ALL ON public.sent_message TO agentic_hr_migrator;")


def downgrade() -> None:
    # ---------------------------------------------------------
    # 1. Downgrade 'sent_messages'
    # ---------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS public.sent_message;")
    drop_tenant_rls_policies("public.sent_messages")
    disable_rls("public.sent_messages")

    op.drop_index("ix_sent_messages_status", table_name="sent_messages")
    op.drop_index("ix_sent_messages_recipient_email", table_name="sent_messages")
    op.drop_index("ix_sent_messages_tenant_provider_msg_id", table_name="sent_messages")
    op.drop_index("ix_sent_messages_tenant_idempotency", table_name="sent_messages")
    op.drop_index("ix_sent_messages_tenant_id", table_name="sent_messages")
    op.drop_table("sent_messages")

    # ---------------------------------------------------------
    # 2. Downgrade 'connector_credentials'
    # ---------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS public.connector_credential;")
    drop_tenant_rls_policies("public.connector_credentials")
    disable_rls("public.connector_credentials")

    op.drop_index("ix_connector_credentials_status", table_name="connector_credentials")
    op.drop_index("ix_connector_credentials_tenant_provider", table_name="connector_credentials")
    op.drop_index("ix_connector_credentials_tenant_id", table_name="connector_credentials")
    op.drop_table("connector_credentials")
