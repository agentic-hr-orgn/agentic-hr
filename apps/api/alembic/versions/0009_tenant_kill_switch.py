"""add tenant kill switch and suspension metadata with rls update policy

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09 17:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add kill-switch and suspension metadata columns to 'tenants'
    op.add_column(
        "tenants",
        sa.Column(
            "is_suspended",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Kill-switch suspension flag (True = suspended, False = active)",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "suspended_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when tenant was suspended",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "suspended_by",
            sa.String(length=128),
            nullable=True,
            comment="Actor ID who engaged the kill-switch",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "suspension_reason",
            sa.Text(),
            nullable=True,
            comment="Administrative reason for tenant suspension",
        ),
    )

    # 2. Index on is_suspended for high-frequency kill-switch checks
    op.create_index("ix_tenants_is_suspended", "tenants", ["is_suspended"])

    # 3. Grant UPDATE privileges to application runtime role on kill-switch columns
    op.execute(
        "GRANT UPDATE (is_suspended, status, suspended_at, suspended_by, suspension_reason, updated_at) "
        "ON public.tenants TO agentic_hr_app;"
    )

    # 4. Grant SELECT on is_suspended to tenant resolver role
    op.execute("GRANT SELECT (is_suspended) ON public.tenants TO agentic_hr_tenant_resolver;")

    # 5. RLS Update policy on 'tenants' (allows updating own tenant under app.tenant_id)
    op.execute("DROP POLICY IF EXISTS tenants_update_policy ON public.tenants;")
    op.execute("""
    CREATE POLICY tenants_update_policy ON public.tenants
    FOR UPDATE
    USING (
        id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    )
    WITH CHECK (
        id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    );
    """)

    # 6. Update resolve_tenant_by_clerk_org to resolve active and suspended tenants
    # (Enables owner restore while inactive/archived tenants remain unresolvable)
    op.execute("""
    CREATE OR REPLACE FUNCTION public.resolve_tenant_by_clerk_org(p_clerk_org_id text)
    RETURNS uuid
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $fn$
        SELECT t.id
        FROM public.tenants t
        WHERE t.clerk_org_id = p_clerk_org_id
          AND t.status IN ('active', 'suspended');
    $fn$;
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenants_update_policy ON public.tenants;")
    op.drop_index("ix_tenants_is_suspended", table_name="tenants")
    op.drop_column("tenants", "suspension_reason")
    op.drop_column("tenants", "suspended_by")
    op.drop_column("tenants", "suspended_at")
    op.drop_column("tenants", "is_suspended")
