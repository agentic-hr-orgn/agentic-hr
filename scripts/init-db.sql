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
