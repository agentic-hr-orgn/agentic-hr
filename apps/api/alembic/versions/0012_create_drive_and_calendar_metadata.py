"""create drive_files and calendar_events tables with rls policies

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11 16:30:00.000000

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
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Create 'drive_files' table
    # ---------------------------------------------------------
    op.create_table(
        "drive_files",
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
        sa.Column("drive_file_id", sa.String(length=255), nullable=False),
        sa.Column("folder_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("web_view_link", sa.String(length=512), nullable=True),
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

    op.create_index(
        "ix_drive_files_tenant_id",
        "drive_files",
        ["tenant_id"],
    )
    op.create_index(
        "ix_drive_files_drive_file_id",
        "drive_files",
        ["drive_file_id"],
    )
    op.create_index(
        "ix_drive_files_tenant_folder",
        "drive_files",
        ["tenant_id", "folder_id"],
    )

    enable_rls("public.drive_files")
    create_tenant_rls_policies("public.drive_files")

    # ---------------------------------------------------------
    # 2. Create 'calendar_events' table
    # ---------------------------------------------------------
    op.create_table(
        "calendar_events",
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
        sa.Column("google_event_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("hangout_link", sa.String(length=512), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Asia/Karachi",
            nullable=False,
        ),
        sa.Column(
            "attendees",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="confirmed",
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
    )

    op.create_index(
        "ix_calendar_events_tenant_id",
        "calendar_events",
        ["tenant_id"],
    )
    op.create_index(
        "ix_calendar_events_google_event_id",
        "calendar_events",
        ["google_event_id"],
    )
    op.create_index(
        "ix_calendar_events_tenant_start",
        "calendar_events",
        ["tenant_id", "start_time"],
    )

    enable_rls("public.calendar_events")
    create_tenant_rls_policies("public.calendar_events")

    # ---------------------------------------------------------
    # 3. Create view aliases for singular/plural mapping
    # ---------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'drive_file' AND schemaname = 'public') THEN
                CREATE VIEW public.drive_file AS SELECT * FROM public.drive_files;
                ALTER VIEW public.drive_file OWNER TO postgres;
                GRANT SELECT, INSERT, UPDATE, DELETE ON public.drive_file TO agentic_hr_app;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'calendar_event' AND schemaname = 'public') THEN
                CREATE VIEW public.calendar_event AS SELECT * FROM public.calendar_events;
                ALTER VIEW public.calendar_event OWNER TO postgres;
                GRANT SELECT, INSERT, UPDATE, DELETE ON public.calendar_event TO agentic_hr_app;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.calendar_event CASCADE;")
    op.execute("DROP VIEW IF EXISTS public.drive_file CASCADE;")

    drop_tenant_rls_policies("public.calendar_events")
    disable_rls("public.calendar_events")
    op.drop_table("calendar_events")

    drop_tenant_rls_policies("public.drive_files")
    disable_rls("public.drive_files")
    op.drop_table("drive_files")
