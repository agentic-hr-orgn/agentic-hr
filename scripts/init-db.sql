-- Agentic-HR Database Initialization Script
-- Automatically executed on initial container creation

CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Create Migration Owner Role
DO $do$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agentic_hr_migrator') THEN
        CREATE ROLE agentic_hr_migrator WITH LOGIN PASSWORD 'agentic_hr_migrator_secret' CREATEDB NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$do$;

-- 2. Create Dedicated Tenant Resolver Role (Function Owner)
DO $do$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agentic_hr_tenant_resolver') THEN
        CREATE ROLE agentic_hr_tenant_resolver WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
    ELSE
        ALTER ROLE agentic_hr_tenant_resolver WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
    END IF;
END
$do$;

-- 3. Create Application Runtime Role
DO $do$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agentic_hr_app') THEN
        CREATE ROLE agentic_hr_app WITH LOGIN PASSWORD 'agentic_hr_app_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    ELSE
        ALTER ROLE agentic_hr_app WITH LOGIN PASSWORD 'agentic_hr_app_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$do$;

-- 4. Schema Permissions & Hardening
GRANT ALL ON SCHEMA public TO agentic_hr_migrator;
GRANT USAGE ON SCHEMA public TO agentic_hr_tenant_resolver;
GRANT USAGE ON SCHEMA public TO agentic_hr_app;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM agentic_hr_app;
REVOKE CREATE ON SCHEMA public FROM agentic_hr_tenant_resolver;

-- 5. Default Privileges for Application Runtime Role
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agentic_hr_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO agentic_hr_migrator;

-- Ensure explicit grants on all Stage 3 tables if they already exist
DO $do$
DECLARE
    tbl text;
    tables text[] := ARRAY[
        'connector_credentials',
        'sent_messages',
        'tenant_spreadsheets',
        'sheets_sync_queue',
        'sheets_drift_logs',
        'drive_files',
        'calendar_events'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables LOOP
        IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = tbl) THEN
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO agentic_hr_app', tbl);
            EXECUTE format('GRANT ALL ON TABLE public.%I TO agentic_hr_migrator', tbl);
        END IF;
    END LOOP;
END
$do$;

