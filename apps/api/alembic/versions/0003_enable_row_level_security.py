"""enable row level security and tenant isolation

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
from app.db.rls import (
    create_tenant_rls_policies,
    disable_rls,
    drop_tenant_rls_policies,
    enable_rls,
)

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Provision Database Roles (Idempotent DO block)
    # ---------------------------------------------------------
    op.execute("""
    DO $do$
    BEGIN
        -- 1a. Migration / Schema Owner Role
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agentic_hr_migrator') THEN
            CREATE ROLE agentic_hr_migrator WITH LOGIN PASSWORD 'agentic_hr_migrator_secret' CREATEDB NOSUPERUSER NOBYPASSRLS;
        ELSE
            ALTER ROLE agentic_hr_migrator WITH LOGIN PASSWORD 'agentic_hr_migrator_secret' CREATEDB NOSUPERUSER NOBYPASSRLS;
        END IF;

        -- 1b. Dedicated Tenant Resolver Role (Function Owner)
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agentic_hr_tenant_resolver') THEN
            CREATE ROLE agentic_hr_tenant_resolver WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
        ELSE
            ALTER ROLE agentic_hr_tenant_resolver WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
        END IF;

        -- 1c. Application Runtime Role
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agentic_hr_app') THEN
            CREATE ROLE agentic_hr_app WITH LOGIN PASSWORD 'agentic_hr_app_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
        ELSE
            ALTER ROLE agentic_hr_app WITH LOGIN PASSWORD 'agentic_hr_app_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
        END IF;
    END
    $do$;
    """)

    # ---------------------------------------------------------
    # 2. Schema Hardening & Table Ownership
    # ---------------------------------------------------------
    op.execute("GRANT ALL ON SCHEMA public TO agentic_hr_migrator;")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC;")
    op.execute("REVOKE CREATE ON SCHEMA public FROM agentic_hr_app;")
    op.execute("REVOKE CREATE ON SCHEMA public FROM agentic_hr_tenant_resolver;")

    op.execute("ALTER TABLE public.roles OWNER TO agentic_hr_migrator;")
    op.execute("ALTER TABLE public.tenants OWNER TO agentic_hr_migrator;")
    op.execute("ALTER TABLE public.users OWNER TO agentic_hr_migrator;")
    op.execute("ALTER TABLE public.memberships OWNER TO agentic_hr_migrator;")

    # ---------------------------------------------------------
    # 3. Privileges for Application Runtime Role (agentic_hr_app)
    # ---------------------------------------------------------
    op.execute("GRANT USAGE ON SCHEMA public TO agentic_hr_app;")
    op.execute("GRANT SELECT ON public.roles TO agentic_hr_app;")
    op.execute("GRANT SELECT ON public.tenants TO agentic_hr_app;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.memberships TO agentic_hr_app;")
    op.execute("REVOKE TRUNCATE ON public.memberships FROM agentic_hr_app;")

    # Column-level restrictions on users table:
    op.execute("GRANT SELECT ON public.users TO agentic_hr_app;")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.users FROM agentic_hr_app;")
    op.execute(
        "GRANT INSERT (id, clerk_user_id, email, name, created_at, updated_at) "
        "ON public.users TO agentic_hr_app;"
    )
    op.execute("GRANT UPDATE (name, updated_at) ON public.users TO agentic_hr_app;")

    # ---------------------------------------------------------
    # 4. Privileges for Tenant Resolver Role (agentic_hr_tenant_resolver)
    # ---------------------------------------------------------
    op.execute("GRANT USAGE ON SCHEMA public TO agentic_hr_tenant_resolver;")
    op.execute(
        "REVOKE ALL ON public.roles, public.users, public.memberships FROM agentic_hr_tenant_resolver;"
    )
    op.execute(
        "GRANT SELECT (id, clerk_org_id, status) ON public.tenants TO agentic_hr_tenant_resolver;"
    )

    # ---------------------------------------------------------
    # 5. Hardened Tenant Resolution Function
    # ---------------------------------------------------------
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
          AND t.status = 'active';
    $fn$;
    """)

    op.execute(
        "ALTER FUNCTION public.resolve_tenant_by_clerk_org(text) OWNER TO agentic_hr_tenant_resolver;"
    )
    op.execute("REVOKE ALL ON FUNCTION public.resolve_tenant_by_clerk_org(text) FROM PUBLIC;")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.resolve_tenant_by_clerk_org(text) TO agentic_hr_app;"
    )

    # ---------------------------------------------------------
    # 6. Enable and Force RLS on Tables
    # ---------------------------------------------------------
    enable_rls("public.tenants")
    enable_rls("public.users")
    enable_rls("public.memberships")

    # ---------------------------------------------------------
    # 7. RLS Policies on 'tenants'
    # ---------------------------------------------------------
    op.execute("DROP POLICY IF EXISTS tenants_select_policy ON public.tenants;")
    op.execute("""
    CREATE POLICY tenants_select_policy ON public.tenants
    FOR SELECT
    USING (
        id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    );
    """)

    # ---------------------------------------------------------
    # 8. RLS Policies on 'users'
    # ---------------------------------------------------------
    op.execute("DROP POLICY IF EXISTS users_select_policy ON public.users;")
    op.execute("""
    CREATE POLICY users_select_policy ON public.users
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.memberships m
            WHERE m.user_id = users.id
              AND m.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        )
        OR
        (
            (
                clerk_user_id = NULLIF(current_setting('app.clerk_user_id', true), '')
                OR email = NULLIF(current_setting('app.clerk_user_id', true), '')
            )
            AND NULLIF(current_setting('app.clerk_user_id', true), '') IS NOT NULL
        )
    );
    """)

    op.execute("DROP POLICY IF EXISTS users_insert_policy ON public.users;")
    op.execute("""
    CREATE POLICY users_insert_policy ON public.users
    FOR INSERT
    WITH CHECK (
        clerk_user_id = NULLIF(current_setting('app.clerk_user_id', true), '')
        AND clerk_user_id IS NOT NULL
    );
    """)

    op.execute("DROP POLICY IF EXISTS users_update_policy ON public.users;")
    op.execute("""
    CREATE POLICY users_update_policy ON public.users
    FOR UPDATE
    USING (
        clerk_user_id = NULLIF(current_setting('app.clerk_user_id', true), '')
        AND clerk_user_id IS NOT NULL
    )
    WITH CHECK (
        clerk_user_id = NULLIF(current_setting('app.clerk_user_id', true), '')
        AND clerk_user_id IS NOT NULL
    );
    """)

    op.execute("DROP POLICY IF EXISTS users_delete_policy ON public.users;")
    op.execute("""
    CREATE POLICY users_delete_policy ON public.users
    FOR DELETE
    USING (
        false
    );
    """)

    # ---------------------------------------------------------
    # 9. RLS Policies on 'memberships'
    # ---------------------------------------------------------
    create_tenant_rls_policies("public.memberships", "tenant_id")


def downgrade() -> None:
    # 1. Drop policies and disable RLS
    drop_tenant_rls_policies("public.memberships")
    disable_rls("public.memberships")

    op.execute("DROP POLICY IF EXISTS users_delete_policy ON public.users;")
    op.execute("DROP POLICY IF EXISTS users_update_policy ON public.users;")
    op.execute("DROP POLICY IF EXISTS users_insert_policy ON public.users;")
    op.execute("DROP POLICY IF EXISTS users_select_policy ON public.users;")
    disable_rls("public.users")

    op.execute("DROP POLICY IF EXISTS tenants_select_policy ON public.tenants;")
    disable_rls("public.tenants")

    # 2. Drop Function
    op.execute("DROP FUNCTION IF EXISTS public.resolve_tenant_by_clerk_org(text);")

    # 3. Revoke grants
    op.execute("REVOKE ALL ON public.tenants FROM agentic_hr_tenant_resolver;")
    op.execute("REVOKE ALL ON SCHEMA public FROM agentic_hr_tenant_resolver;")

    op.execute("REVOKE ALL ON public.memberships FROM agentic_hr_app;")
    op.execute("REVOKE ALL ON public.users FROM agentic_hr_app;")
    op.execute("REVOKE ALL ON public.tenants FROM agentic_hr_app;")
    op.execute("REVOKE ALL ON public.roles FROM agentic_hr_app;")
    op.execute("REVOKE ALL ON SCHEMA public FROM agentic_hr_app;")
