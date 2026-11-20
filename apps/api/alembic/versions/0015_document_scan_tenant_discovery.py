"""stage 4 prompt 5: narrow SECURITY DEFINER active tenant discovery for document expiry scan

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-21 01:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ensure agentic_hr_tenant_resolver has column-scoped SELECT on tenants
    op.execute(
        "GRANT SELECT (id, status, is_suspended) ON public.tenants TO agentic_hr_tenant_resolver;"
    )

    # Narrow SECURITY DEFINER function returning ONLY active, non-suspended tenant UUIDs.
    # Matches Stage 2 Pass 1 M5 (public.outbox_due_tenant_ids) zero-PII discovery pattern.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.document_scan_tenant_ids(
            p_limit integer DEFAULT 500
        )
        RETURNS SETOF uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT t.id
            FROM public.tenants t
            WHERE t.status = 'active'
              AND COALESCE(t.is_suspended, false) = false
            ORDER BY t.id
            LIMIT GREATEST(COALESCE(p_limit, 500), 1);
        $fn$;
        """
    )

    op.execute(
        "ALTER FUNCTION public.document_scan_tenant_ids(integer) "
        "OWNER TO agentic_hr_tenant_resolver;"
    )
    op.execute("REVOKE ALL ON FUNCTION public.document_scan_tenant_ids(integer) FROM PUBLIC;")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.document_scan_tenant_ids(integer) TO agentic_hr_app;"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.document_scan_tenant_ids(integer);")
