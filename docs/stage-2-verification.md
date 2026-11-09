# Stage 2: Platform Core — Master Requirements Verification & Exit Gate Audit Report

**Date of Verification:** 2026-11-09  
**Platform Version:** Agentic HR Core — Stage 2 (Platform Core)  
**Execution Environment:** Windows Server / Docker Containerized PostgreSQL 16 + pgvector, Redis, Temporal  
**Active Integration Mode:** `USE_FAKE_INTEGRATIONS=true` (Synthetic Sandbox Mode)  
**Audit Status:** **STAGE 2 EXIT GATE PASSED**

---

## 1. Executive Summary & Scope

Stage 2 ("Platform Core") establishes the mission-critical, enterprise-grade multi-tenant foundation, cryptographic security boundaries, durable workflow orchestration, and provider-neutral AI routing for **Agentic HR Core**.

This audit report represents the final, evidence-based verification of all 12 Stage 2 prompt deliverables against the actual codebase, live database policies, and automated test execution. In accordance with project instructions, all existing Stage 2 work has been strictly preserved; the repository implementation and fresh command outputs serve as the authoritative source of truth.

### Key Stage 2 Architectural Guarantees Verified
1. **Multi-Tenant Isolation**: Enforced at the PostgreSQL engine level via Row-Level Security (RLS) under a restricted non-superuser runtime role (`agentic_hr_app`).
2. **Global Identity vs. Tenant Scoping**: Table-specific policies distinguish global user records (`public.users`) from tenant-scoped entities. The self-identity bootstrap branch on `public.users` returns solely the caller's own record and cannot access tenant data when `app.tenant_id` is unset.
3. **Restricted SECURITY DEFINER Tenant Resolver**: `resolve_tenant_by_clerk_org()` runs under a dedicated `NOLOGIN`, `BYPASSRLS` role (`agentic_hr_tenant_resolver`) with fixed `search_path = pg_catalog, pg_temp`, returning only scalar UUIDs.
4. **Append-Only Auditing**: PostgreSQL trigger `prevent_audit_log_modification()` rejects `UPDATE` and `DELETE` queries with SQLSTATE `55000`. Audit records are atomically enriched with client IP, user agent, and request ID via middleware `contextvars`.
5. **Role-Based & Type-Specific Approvals**: Specialized approval types (`payroll_override`, `policy_exception`, `candidate_offer`, `leave_request`) enforce specific permission flags (`is_payroll_admin`, `is_hr_head`) alongside role hierarchy (`owner`, `manager`), anti-self-approval, and multi-round append-only revision histories.
6. **Temporal Durable Orchestration**: `HumanApprovalWorkflow` deterministically yields execution via `@workflow.signal` handlers and double-checks state in PostgreSQL via an activity before completing, surviving worker shutdowns and restarts.
7. **Transactional Outbox & Concurrent Idempotency**: Atomic outbox pattern with `SELECT ... FOR UPDATE SKIP LOCKED` and concurrent idempotency locking preventing duplicate business actions.
8. **Cryptographic Protection**: AES-256-GCM envelope encryption with per-record DEKs, master KEK wrapping, canonical sorted JSON AAD binding, and zero-downtime key rotation.
9. **Infrastructure Governance**: Atomic Redis sliding-window rate limiting and database-backed tenant kill switch (`is_suspended`) returning HTTP 403 `TENANT_SUSPENDED`.
10. **Provider-Neutral Model Gateway**: Three-tier routing (`cheap`, `strong`, `private`) driven entirely by environment variables with zero hardcoded model IDs, bounded retries (`MODEL_GATEWAY_MAX_RETRIES = 2`), single structured repair attempt, untrusted input safe encoding and restriction-only authorization policy, offline fake provider, and zero-PII tracing.

---

## 2. Requirements Compliance Checklist

| Area | Requirement Description | Implementation Reference | Verification Test / Evidence | Status |
| :---: | :--- | :--- | :--- | :---: |
| **01** | **Identity, Clerk JWT, Memberships & RBAC** | `apps/api/app/models/tenant.py`<br>`apps/api/app/core/clerk.py`<br>`apps/api/app/core/security.py` | `test_clerk_auth.py`<br>`test_identity_tenancy_models.py`<br>`test_multi_tenant_isolation.py` | **PASS** |
| **02** | **Tenant Context & Table-Specific RLS** | `apps/api/app/core/database.py`<br>`apps/api/alembic/versions/0003_enable_row_level_security.py` | `test_tenant_context.py`<br>`test_database_rls.py`<br>`test_multi_tenant_isolation.py` | **PASS** |
| **03** | **Restricted DB Roles & Tenant Resolver** | `apps/api/alembic/versions/0003_enable_row_level_security.py`<br>`apps/api/tests/isolation_harness.py` | `test_database_rls.py`<br>`test_multi_tenant_isolation.py` | **PASS** |
| **04** | **Immutable Audit Log & Context Enrichment** | `apps/api/alembic/versions/0004_create_audit_logs.py`<br>`apps/api/app/core/middleware.py`<br>`apps/api/app/services/audit_service.py` | `test_audit_logging.py` | **PASS** |
| **05** | **Approvals by Type & Multi-Round Revisions** | `apps/api/app/models/approval.py`<br>`apps/api/app/services/approval_service.py`<br>`apps/api/app/api/v1/endpoints/approvals.py` | `test_approvals.py`<br>`test_approval_corrections.py` | **PASS** |
| **06** | **Temporal Approval Signals & Worker Recovery** | `apps/api/app/workflows/approval_workflow.py`<br>`apps/api/app/workflows/activities.py` | `test_approval_workflow.py`<br>`test_temporal_approval_integration.py` | **PASS** |
| **07** | **Transactional Outbox & Concurrent Idempotency** | `apps/api/app/models/outbox.py`<br>`apps/api/app/services/outbox_publisher.py`<br>`apps/api/app/core/idempotency.py` | `test_outbox_and_idempotency.py` | **PASS** |
| **08** | **AES-256-GCM Envelope Encryption & AAD** | `apps/api/app/core/encryption.py` | `test_field_encryption.py` | **PASS** |
| **09** | **Redis Rate Limiting & Tenant Kill Switch** | `apps/api/app/core/rate_limit.py`<br>`apps/api/app/api/v1/endpoints/tenants.py`<br>`apps/api/app/core/security.py` | `test_rate_limit_and_kill_switch.py` | **PASS** |
| **10** | **Model Gateway Routes & Env Configuration** | `apps/api/app/core/model_gateway.py`<br>`apps/api/app/core/config.py` | `test_model_gateway.py` | **PASS** |
| **11** | **Structured Output, Repair & Untrusted Defense** | `apps/api/app/core/model_gateway.py`<br>`apps/api/app/core/untrusted.py` | `test_model_gateway.py` | **PASS** |
| **12** | **Fake Model Provider & Tracing Privacy** | `apps/api/app/core/fake_model.py`<br>`apps/api/app/core/model_gateway.py` | `test_model_gateway.py` | **PASS** |

---

## 3. Database Schema, Migrations & Role Inventory

### 3.1 Migration Ledger
All 9 Alembic migrations are applied and confirmed at head (`0009`):
1. `0001_enable_pgvector.py`: Installs `pgvector` extension for semantic embedding storage.
2. `0002_identity_and_tenancy.py`: Creates `roles`, `tenants`, `users`, and `memberships` tables.
3. `0003_enable_row_level_security.py`: Creates least-privilege roles, enforces RLS policies, and creates SECURITY DEFINER `resolve_tenant_by_clerk_org()`.
4. `0004_create_audit_logs.py`: Creates append-only `audit_logs` table with SQLSTATE `55000` immutability trigger.
5. `0005_create_approvals.py`: Creates domain `approvals` table and state machine constraints.
6. `0006_approval_corrections_and_revisions.py`: Creates append-only `approval_revisions` table for multi-round human resubmissions.
7. `0007_add_workflow_id_to_approvals.py`: Adds Temporal `workflow_id` and `workflow_run_id` to `approvals`.
8. `0008_outbox_and_idempotency.py`: Creates transactional `outbox_events` and `idempotency_keys` tables with RLS.
9. `0009_tenant_kill_switch.py`: Adds `is_suspended` boolean flag to `tenants` with restricted column-level grants.

### 3.2 Database Roles Privilege Matrix

| Database Role | Login Allowed | Superuser | Bypass RLS | Granted Schema Privileges | Specific Object Restrictions |
| :--- | :---: | :---: | :---: | :--- | :--- |
| `agentic_hr_migrator` | YES | NO | YES | `ALL ON SCHEMA public` | Owns all tables, functions, and sequences. Used exclusively by Alembic DDL migrations. |
| `agentic_hr_app` | YES | **NO** | **NO** | `USAGE ON SCHEMA public` | Runtime application role. `SELECT` on roles/tenants; `SELECT, INSERT, UPDATE, DELETE` on memberships/outbox/idempotency; `SELECT, INSERT` on audit_logs (NO UPDATE/DELETE); `SELECT, INSERT, UPDATE` on approvals (NO DELETE). Immutable columns on `users` (`clerk_user_id`, `email`). |
| `agentic_hr_tenant_resolver` | **NO** | **NO** | **YES** | `USAGE ON SCHEMA public` | Owns `resolve_tenant_by_clerk_org(text)`. `SELECT (id, clerk_org_id, status) ON public.tenants`. Cannot login. |

---

## 4. Row-Level Security & Tenant Isolation Evidence

### 4.1 Proof of Restricted Runtime Role Execution
Tests in `apps/api/tests/test_database_rls.py` verify database role attributes directly against `pg_catalog.pg_roles`:
- `test_runtime_role_privileges`: Confirms `agentic_hr_app` has `rolsuper = False` and `rolbypassrls = False`.
- `test_runtime_role_not_owner`: Confirms tables are owned by `agentic_hr_migrator`, NOT `agentic_hr_app`, with `relrowsecurity = True` and `relforcerowsecurity = True`.
- `test_column_level_privileges_restricted`: Confirms `UPDATE users SET clerk_user_id = 'hacked'` raises permission denied.

### 4.2 Table-Specific RLS Policies
The schema enforces distinct RLS policies based on data model architecture:
1. **`public.users` (Global Identity Model)**:
   - `users_select_policy`: Permits query if caller belongs to tenant via `public.memberships` (`m.tenant_id = app.tenant_id`) **OR** the record matches the caller's self-identity (`clerk_user_id = app.clerk_user_id`).
   - `users_insert_policy` & `users_update_policy`: Strictly scoped to self (`clerk_user_id = app.clerk_user_id`).
   - `users_delete_policy`: `USING (false)` (application role cannot delete users).
2. **`public.tenants`**: Scoped strictly by `id = app.tenant_id::uuid`.
3. **`public.memberships`**: Scoped strictly by `tenant_id = app.tenant_id::uuid`.
4. **`public.audit_logs`**: Scoped strictly by `tenant_id = app.tenant_id::uuid`.
5. **`public.approvals`**: Scoped strictly by `tenant_id = app.tenant_id::uuid`.
6. **`public.approval_revisions`**: Scoped strictly by `tenant_id = app.tenant_id::uuid`.
7. **`public.outbox_events`**: Scoped strictly by `tenant_id = app.tenant_id::uuid`.
8. **`public.idempotency_keys`**: Scoped strictly by `tenant_id = app.tenant_id::uuid`.

### 4.3 Behavioral Verification of Safeguard 1 (Self-Identity vs. Active Tenant Context)
A direct database test verified what data is accessible when `app.tenant_id` is unset (NULL) but `app.clerk_user_id` is set:
- `public.users`: Exactly **1 row** returned (the caller's own user record). Other users: **0 rows**.
- `public.tenants`: **0 rows**.
- `public.memberships`: **0 rows**.
- `public.approvals`: **0 rows**.
- `public.outbox_events`: **0 rows**.
- `public.audit_logs`: **0 rows**.

**Finding**: The self-identity condition on `public.users` cannot bypass the requirement for an active tenant context to access tenant entities. All tenant-scoped tables remain strictly fail-closed.

---

## 5. SECURITY DEFINER Tenant Resolver Analysis

Inspection of `resolve_tenant_by_clerk_org(p_clerk_org_id text)` in `0003_enable_row_level_security.py` (lines 102–124) confirms:
- **Role & Ownership**: Owned by `agentic_hr_tenant_resolver` with `NOLOGIN` and `BYPASSRLS`.
- **Search Path Hardening**: Explicitly declares `SET search_path = pg_catalog, pg_temp`, preventing search path hijacking.
- **Qualified Object References**: Queries `public.tenants t WHERE t.clerk_org_id = p_clerk_org_id AND t.status = 'active'`.
- **Information Leakage Defense**: Returns only scalar `uuid` (`id`). Does not expose tenant names, billing statuses, or user counts.
- **Execution Grants**: Revoked from `PUBLIC`; granted exclusively to `agentic_hr_app`.
- **Negative Tests**: Verified in `test_database_rls.py`:
  - `test_resolver_role_cannot_login`: Confirms role cannot establish database sessions.
  - `test_resolver_function_revoked_from_public`: Confirms `has_function_privilege('public', ..., 'EXECUTE')` is False.
  - `test_resolver_invalid_org_returns_null`: Unmatched organization returns NULL.
  - `test_resolver_inactive_tenant_returns_null`: Suspended or inactive tenant returns NULL.

---

## 6. Authentication, RBAC & Audit Evidence

### 6.1 Authentication & Multi-Tenancy Resolution
- **Clerk Integration**: Offline RS256 JWKS signature verification (`apps/api/app/core/clerk.py`).
- **Organization Resolution**: Tenant ID is derived from verified JWT `org_id` claims, validated against PostgreSQL memberships.
- **Role Hierarchy**: Enforced in `apps/api/app/core/security.py`:
  - `owner` > `manager` > `employee`.
  - Anti-self-promotion: Users cannot elevate their own role (`test_role_rbac_owner_anti_self_promotion`).
  - Employees cannot access manager or owner endpoints (HTTP 403 Forbidden).

### 6.2 Immutable Auditing & Request Contextvars Enrichment
- **Immutability Trigger**: `prevent_audit_log_modification()` raises exception with SQLSTATE `55000` on any `UPDATE` or `DELETE` attempt on `public.audit_logs`.
- **Middleware Integration**: `RequestIDMiddleware` (`apps/api/app/core/middleware.py`) captures:
  - `request_id_ctx`: Unique X-Request-ID.
  - `client_ip_ctx`: Client IP address from `x-forwarded-for` or direct socket.
  - `user_agent_ctx`: User agent header string.
- **Transactional Writing**: `write_audit()` (`apps/api/app/services/audit_service.py`) automatically enqueues audit entries within the caller's active database transaction, embedding `_request` metadata and redacting Pakistani CNICs and credentials.

---

## 7. Approval Permissions by Type & Temporal Workflow Evidence

### 7.1 Approval Authorization Matrix

| Approval Type | Required Role / Permission Flag | Authorized Roles | Unauthorized Roles Blocked |
| :--- | :--- | :--- | :--- |
| `leave_request` | `manager` | Owner, Manager | Employee (HTTP 403) |
| `candidate_offer` | `owner` | Owner | Manager, Employee (HTTP 403) |
| `payroll_override` | `payroll_admin` | Owner, User with `is_payroll_admin=True` | General Manager without flag, Employee (HTTP 403) |
| `policy_exception` | `hr_head` | Owner, User with `is_hr_head=True` | General Manager without flag, Employee (HTTP 403) |

- **Anti-Self-Approval**: Verified in `test_10_anti_self_approval_rule` (requester cannot approve their own request, even if they hold the required role).
- **Multi-Round Corrections**: Handled via `approval_revisions` table (`test_approval_corrections.py`), tracking revision numbers, previous payloads, and change request reasons.

### 7.2 Temporal Durable Orchestration
- **Deterministic Signal Waiting**: `HumanApprovalWorkflow` yields execution using `await workflow.wait_condition(lambda: self.decision_signal is not None)`.
- **Authoritative Database Verification**: Upon signal receipt, the workflow invokes `verify_approval_record_activity` to confirm PostgreSQL database state matches the signal before completing.
- **Worker Restart Resilience**: `test_01_exact_worker_restart_scenario` confirms workflow execution state is preserved across complete worker process termination and restart.

---

## 8. Events, Encryption, Rate Limiting & Kill Switch

### 8.1 Transactional Outbox & Concurrent Idempotency
- **Worker Concurrency**: `outbox_publisher.py` claims events using `SELECT ... FOR UPDATE SKIP LOCKED` with exponential retry backoff and dead-letter queue routing (`test_04_concurrent_outbox_skip_locked`).
- **Concurrent Idempotency**: `test_05_concurrent_idempotency_requests` proves that multiple concurrent requests with identical idempotency keys execute safely without duplicate transactions.

### 8.2 Envelope Encryption & Key Management
- **Cryptographic Standard**: AES-256-GCM envelope encryption with 256-bit ephemeral DEKs and master KEK wrapping (`apps/api/app/core/encryption.py`).
- **AAD Canonical Binding**: Binds `tenant_id`, `table_name`, `field_name`, and `record_id` as canonical sorted JSON. Mismatched AAD raises `AuthenticationFailedError`.
- **Zero-Downtime Rotation**: Verified in `test_11_key_id_selection_and_historical_rotation` (historical records decrypt using secondary keys while new writes use the active key).

### 8.3 Rate Limiting & Tenant Governance
- **Redis Sliding Window**: Powered by atomic Redis Lua script (`rate_limit.lua`) enforcing per-tenant (1200 RPM), per-user (300 RPM), and public (60 RPM) quotas.
- **Tenant Kill Switch**: Database column `public.tenants.is_suspended` checked on every authenticated request. Suspended tenants immediately receive HTTP 403 Forbidden with `TENANT_SUSPENDED`. Toggling suspension emits immutable audit records.

---

## 9. Central Model Gateway & Privacy Evidence

### 9.1 Route Matrix & Configuration-Driven IDs
All model configurations are driven strictly by environment variables with zero hardcoded defaults:
- **`cheap` route**: Sourced from `CHEAP_MODEL_PROVIDER` and `CHEAP_MODEL_ID`.
- **`strong` route**: Sourced from `STRONG_MODEL_PROVIDER` and `STRONG_MODEL_ID`.
- **`private` route**: Sourced from `PRIVATE_MODEL_PROVIDER`, `PRIVATE_MODEL_ID`, and `PRIVATE_MODEL_ENDPOINT`.

### 9.2 Fail-Closed Private Route
- `test_04_private_route_unavailable_fails_closed` and `test_15_private_route_missing_endpoint_fails_closed`: Missing or invalid private route configuration raises `ModelRouteUnavailableError`.
- `test_16_private_route_no_fallback_to_public`: Verifies that an unavailable private route **never** silently falls back to public models.

### 9.3 Structured Output & Single Repair Separation
- `structured_output(schema, prompt)` validates responses against Pydantic models.
- Initial validation failure triggers exactly **one** repair attempt on the original approved route.
- Second failure raises controlled `ModelValidationError`.
- Provider retry attempts (`MODEL_GATEWAY_MAX_RETRIES = 2`, maximum 3 total provider attempts) are strictly reserved for transient transport errors (429, 503, timeouts), completely separated from schema repair. Permanent errors (400, 401, 403, 404) fail immediately on attempt 1.

### 9.4 Untrusted Data Defense & Tool Authorization Policy
- `safe_encode_untrusted_text`: Strips null bytes and escapes closing boundary tags (`</untrusted_...>` -> `&lt;/untrusted_...&gt;`).
- `enforce_untrusted_context_policy`: Restriction-only helper. Raises `ToolAuthorizationError` when an untrusted context attempts an unapproved or mutating tool. Returns `None` on pass (does NOT grant positive authorization or replace RBAC checks).

### 9.5 Privacy-Preserving Tracing
- `LANGFUSE_TRACING_ENABLED: bool = False` by default.
- In fake mode (`USE_FAKE_INTEGRATIONS=true`), the gateway exclusively utilizes `InMemoryTraceSink` with zero live network calls.
- `test_14_langfuse_privacy_preservation`: Verifies that raw prompts, employee PII, salaries, and model outputs are replaced with `[REDACTED_PRIVACY_BOUNDARY: Usage-only tracing mode active]`.
- `test_21_unknown_cost_is_strictly_none`: Verifies that unknown model costs are represented as `None` (JSON `null`), never falsely reported as zero.

---

## 10. Actual Automated Test & Quality Gate Results

### 10.1 Full Monorepo Quality Check (`scripts/dev.ps1 check`)
```
==========================================================
   Agentic HR Core - Full Quality Verification Gate       
==========================================================
1/5. Linting Python backend (Ruff)...
All checks passed!
95 files already formatted
2/5. Running backend tests (Pytest + HTTPX)...
collected 175 items
apps\api\tests\test_api_endpoints.py ....                                [  2%]
apps\api\tests\test_approval_corrections.py ..............               [ 10%]
apps\api\tests\test_approval_workflow.py ...                             [ 12%]
apps\api\tests\test_approvals.py ...........                             [ 18%]
apps\api\tests\test_audit_logging.py .........                           [ 23%]
apps\api\tests\test_clerk_auth.py .........                              [ 28%]
apps\api\tests\test_database_rls.py .....................                [ 40%]
apps\api\tests\test_fake_integrations.py ......                          [ 44%]
apps\api\tests\test_field_encryption.py .................                [ 53%]
apps\api\tests\test_health.py ...                                        [ 55%]
apps\api\tests\test_identity_tenancy_models.py ........                  [ 60%]
apps\api\tests\test_model_gateway.py .....................               [ 72%]
apps\api\tests\test_multi_tenant_isolation.py ...................        [ 82%]
apps\api\tests\test_outbox_and_idempotency.py .........                  [ 88%]
apps\api\tests\test_rate_limit_and_kill_switch.py ..........             [ 93%]
apps\api\tests\test_temporal_approval_integration.py .....               [ 96%]
apps\api\tests\test_temporal_workflow.py ....                            [ 98%]
apps\api\tests\test_tenant_context.py ..                                 [100%]
====================== 175 passed, 3 warnings in 55.18s =======================

3/5. Running frontend unit tests (Vitest)...
 ✓ src/test/ui-store.test.ts (3 tests) 4ms
 Test Files  1 passed (1)
      Tests  3 passed (3)
   Duration  2.13s

4/5. Typechecking all workspace packages...
packages/contracts typecheck: Done
packages/ui typecheck: Done
apps/web typecheck: Done

5/5. Prettier & ESLint checks...
Checking formatting...
All matched files use Prettier code style!
✔ No ESLint warnings or errors

==> ALL QUALITY CHECKS PASSED!
```

### 10.2 Frontend Production Build & Playwright Browser Smoke Tests
```
$ pnpm --filter @agentic-hr/web build
✓ Compiled successfully
✓ Generating static pages (10/10)
Route (app)                              Size     First Load JS
┌ ƒ /                                    5.96 kB         119 kB
├ ƒ /approvals                           7.81 kB         142 kB
├ ƒ /approvals/[id]/correct              4.76 kB         139 kB
└ ...

$ pnpm --filter @agentic-hr/web test:e2e
Running 4 tests using 3 workers
  ok 1 [chromium] › unauthenticated visitors are redirected to sign-in page (4.2s)
  ok 2 [chromium] › sign-up page loads with platform branding and security notice (4.3s)
  ok 3 [chromium] › authenticated dashboard loads with brand, hero section, and navigation (3.3s)
  ok 4 [chromium] › navigation links navigate to placeholder routes (902ms)
  4 passed (6.5s)
```

### 10.3 Focused Model Gateway Test Suite (`apps/api/tests/test_model_gateway.py`)
```
collected 21 items
apps\api\tests\test_model_gateway.py::test_01_cheap_route PASSED         [  4%]
apps\api\tests\test_model_gateway.py::test_02_strong_route PASSED        [  9%]
apps\api\tests\test_model_gateway.py::test_03_private_route_configured PASSED [ 14%]
apps\api\tests\test_model_gateway.py::test_04_private_route_unavailable_fails_closed PASSED [ 19%]
apps\api\tests\test_model_gateway.py::test_05_valid_structured_output PASSED [ 23%]
apps\api\tests\test_model_gateway.py::test_06_structured_output_one_repair_success PASSED [ 28%]
apps\api\tests\test_model_gateway.py::test_07_structured_output_repair_failure PASSED [ 33%]
apps\api\tests\test_model_gateway.py::test_08_connection_timeout PASSED  [ 38%]
apps\api\tests\test_model_gateway.py::test_09_rate_limit_handling PASSED [ 42%]
apps\api\tests\test_model_gateway.py::test_10_retry_with_exponential_backoff PASSED [ 47%]
apps\api\tests\test_model_gateway.py::test_11_prompt_injection_defense PASSED [ 52%]
apps\api\tests\test_model_gateway.py::test_12_fake_model_offline_ci PASSED [ 57%]
apps\api\tests\test_model_gateway.py::test_13_token_usage_and_cost_tracking PASSED [ 61%]
apps\api\tests\test_model_gateway.py::test_14_langfuse_privacy_preservation PASSED [ 66%]
apps\api\tests\test_model_gateway.py::test_15_private_route_missing_endpoint_fails_closed PASSED [ 71%]
apps\api\tests\test_model_gateway.py::test_16_private_route_no_fallback_to_public PASSED [ 76%]
apps\api\tests\test_model_gateway.py::test_17_untrusted_boundary_encoding PASSED [ 80%]
apps\api\tests\test_model_gateway.py::test_18_enforce_untrusted_context_policy PASSED [ 85%]
apps\api\tests\test_model_gateway.py::test_19_permanent_errors_fail_immediately_without_retry PASSED [ 90%]
apps\api\tests\test_model_gateway.py::test_20_retry_and_repair_separation PASSED [ 95%]
apps\api\tests\test_model_gateway.py::test_21_unknown_cost_is_strictly_none PASSED [100%]
============================= 21 passed in 0.26s ==============================
```

---

## 11. Synthetic Seed Data & Secrets Audit

### 11.1 Seeder Execution Verification
- Target database: `settings.MIGRATION_DATABASE_URL` (`localhost:5432/agentic_hr`).
- Output:
  - 2 Tenants: Acme Tech Solutions (`org_acme_tech_01`), Globex Corporation (`org_globex_corp_02`).
  - 7 Users: Authorized ADR 006 test accounts (`agentic.hr.test.ubaid.*`).
  - 8 Memberships: Multi-tenant assignments with explicit role and permission flags.
  - 5 Approvals: Multi-company seeded approvals across all 4 approval types.
- Idempotency: Verified safe and repeatable with zero constraint errors.

### 11.2 Git Secrets Audit
- `.env` is confirmed in `.gitignore` and has never been committed.
- `.env.example` contains exclusively empty placeholders.
- Repository scans confirm zero production secrets, API keys, or private keys in Git history.

---

## 12. Known Limitations & Architectural Boundaries

1. **Synthetic Data Sandbox**: In accordance with ADR 003 and ADR 006, the platform currently operates with synthetic test data under `USE_FAKE_INTEGRATIONS=true`. Production deployment will require live Google Workspace OAuth credentials and an independently audited private model endpoint.
2. **Private Route Status**: The private route is confirmed fail-closed. Real sensitive HR workloads must remain routed only to verified on-premises or enterprise private model endpoints.
3. **Connectors Deferred to Stage 3**: Google Workspace integrations (Gmail, Sheets, Drive, Calendar) are currently represented by deterministic in-memory fake adapters. Live API connectors are scheduled for Stage 3.

---

## 13. Corrected Master Roadmap Handoff (Stages 3 to 9)

In accordance with user instructions, autonomous agents are NOT placed in Stage 3. The verified feature progression is strictly defined as follows:

- **Stage 3: Connectors**: Google Workspace least-privilege connectors (Gmail send-only, Drive file-level, Calendar events, Sheets view), OAuth token vault, one-way background Sheets sync workflow, FastMCP integration.
- **Stage 4: Onboarding & Employee Records**: SME Onboarding Wizard, Employee profile management, Record Keeper, document storage metadata.
- **Stage 5: Policy Ingestion & Search**: Policy document upload, chunking, `gemini-embedding-2` vector embeddings in `pgvector`, semantic search.
- **Stage 6: Leave & Attendance**: Leave request workflows, statutory leave accrual calculations, attendance logging, calendar event sync.
- **Stage 7: Policy Brain & Helpdesk**: Bilingual (English & Urdu) Policy Brain RAG assistant, employee helpdesk, citation-backed policy Q&A.
- **Stage 8: Recruitment Workforce**: Autonomous hiring agents: **DocuVerify** (OCR & credential check), **HR Screener** (rubric scoring), **Code Tester** (E2B sandbox evaluation), **Job Poster**, and **HireFlow** pipeline.
- **Stage 9: Training & Performance**: Employee training track assignments, performance review cycles, 360 feedback workflows.

---

## 14. Final Exit Gate Decision

> [!IMPORTANT]
> ### **STAGE 2 EXIT GATE PASSED**
>
> All 12 Stage 2 prompt deliverables, all 9 Alembic migrations, all database security roles, Row-Level Security policies, immutability triggers, durable Temporal workflows, outbox workers, envelope encryption routines, Redis rate limiters, tenant kill switches, and Model Gateway security controls have been thoroughly audited, inspected, and verified with fresh test evidence.
>
> **Verification Metrics Summary**:
> - Full Monorepo Quality Gate: **PASSED (5/5 steps)**
> - Backend Pytest Full Suite: **175 PASSED** (0 failed)
> - Frontend Unit Suite (Vitest): **3 PASSED** (0 failed)
> - Browser Smoke Suite (Playwright): **4 PASSED** (0 failed)
> - Central Model Gateway Suite: **21 PASSED** (0 failed)
> - Synthetic Database Seeder: **PASSED (100% idempotent & isolated)**
> - Git Secrets Audit: **PASSED (Zero secrets in Git)**
>
> Stage 2 is officially certified complete. The platform is ready to proceed to Stage 3 (Connectors).

