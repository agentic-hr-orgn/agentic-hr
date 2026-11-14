"""security hardening: view rls enforcement, audit truncate protection, and cross-tenant outbox discovery

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-14 11:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALIAS_VIEWS: tuple[str, ...] = (
    "audit_log",
    "approval",
    "approval_revision",
    "outbox_event",
    "idempotency_key",
    "connector_credential",
    "sent_message",
    "sheets_sync_queue_item",
    "sheets_drift_log",
    "drive_file",
    "calendar_event",
)


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. C1: Enforce RLS on all 11 alias views (security_invoker + security_barrier)
    #    and transfer ownership from superuser (postgres) to agentic_hr_migrator (NOBYPASSRLS)
    # ---------------------------------------------------------
    for view in ALIAS_VIEWS:
        op.execute(
            f"""
            DO $do$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_catalog.pg_views
                    WHERE schemaname = 'public' AND viewname = '{view}'
                ) THEN
                    EXECUTE 'ALTER VIEW public.{view} SET (security_invoker = true, security_barrier = true)';
                    EXECUTE 'ALTER VIEW public.{view} OWNER TO agentic_hr_migrator';
                END IF;
            END
            $do$;
            """
        )

    # Ensure audit_log view remains strictly SELECT-only for agentic_hr_app
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.audit_log FROM agentic_hr_app;")
    op.execute("GRANT SELECT ON public.audit_log TO agentic_hr_app;")

    # ---------------------------------------------------------
    # 2. M3: Block TRUNCATE on public.audit_logs via statement-level trigger & privilege revocation
    # ---------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.prevent_audit_log_modification()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Audit log entries are immutable and append-only. UPDATE, DELETE, and TRUNCATE operations are strictly prohibited.'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON public.audit_logs;")
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_truncate
        BEFORE TRUNCATE ON public.audit_logs
        FOR EACH STATEMENT
        EXECUTE FUNCTION public.prevent_audit_log_modification();
        """
    )

    op.execute(
        "REVOKE TRUNCATE ON TABLE public.audit_logs FROM agentic_hr_migrator, agentic_hr_app;"
    )

    # ---------------------------------------------------------
    # 3. M5: Narrow SECURITY DEFINER function for cross-tenant outbox tenant discovery
    #    Owned by agentic_hr_tenant_resolver (BYPASSRLS, NOLOGIN).
    #    Returns strictly tenant_id UUIDs (zero payload or PII leakage).
    # ---------------------------------------------------------
    op.execute(
        "GRANT SELECT (tenant_id, status, next_attempt_at) "
        "ON public.outbox_events TO agentic_hr_tenant_resolver;"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.outbox_due_tenant_ids(
            p_limit integer DEFAULT 50,
            p_stale_seconds integer DEFAULT 300
        )
        RETURNS SETOF uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT DISTINCT e.tenant_id
            FROM public.outbox_events e
            WHERE (e.status = 'pending' AND e.next_attempt_at <= CURRENT_TIMESTAMP)
               OR (
                   e.status = 'processing'
                   AND e.next_attempt_at <= CURRENT_TIMESTAMP - make_interval(secs => p_stale_seconds)
               )
            LIMIT GREATEST(COALESCE(p_limit, 50), 1);
        $fn$;
        """
    )

    op.execute(
        "ALTER FUNCTION public.outbox_due_tenant_ids(integer, integer) "
        "OWNER TO agentic_hr_tenant_resolver;"
    )
    op.execute("REVOKE ALL ON FUNCTION public.outbox_due_tenant_ids(integer, integer) FROM PUBLIC;")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.outbox_due_tenant_ids(integer, integer) "
        "TO agentic_hr_app;"
    )


def downgrade() -> None:
    # 1. Drop outbox_due_tenant_ids function & revoke resolver grant
    op.execute("DROP FUNCTION IF EXISTS public.outbox_due_tenant_ids(integer, integer);")
    op.execute("REVOKE ALL ON public.outbox_events FROM agentic_hr_tenant_resolver;")

    # 2. Drop TRUNCATE trigger on audit_logs and restore grant to migrator
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON public.audit_logs;")
    op.execute("GRANT TRUNCATE ON TABLE public.audit_logs TO agentic_hr_migrator;")

    # 3. Reset view options on alias views
    for view in ALIAS_VIEWS:
        op.execute(
            f"""
            DO $do$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_catalog.pg_views
                    WHERE schemaname = 'public' AND viewname = '{view}'
                ) THEN
                    EXECUTE 'ALTER VIEW public.{view} RESET (security_invoker, security_barrier)';
                END IF;
            END
            $do$;
            """
        )
