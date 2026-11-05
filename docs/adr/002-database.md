# ADR 002: Database - One DB with RLS
Status: Decided
Context: Multi-tenant, one company should never read another's data
Decision: One Postgres DB, row-level security, every table has tenant_id
Consequences: Isolation tested in Stage 2, extended each stage