# ADR 009: Stage 6 Leave and Attendance Architecture

Status: Decided
Date: 2026-11-22

## Context
Stage 6 introduces **Leave and Attendance** for Pakistani SMEs on top of Agentic HR Core's multi-tenant PostgreSQL + Forced RLS foundation (ADR 002), Stage 2 human approval workflows, Temporal durable orchestration (ADR 001), and Postgres-authoritative Google Sheets sync (ADR 005).

Consistent with **Rule 3 (LLM proposes, code decides)**, every leave balance calculation, working-day count, probation/notice/team-coverage policy check, attendance state transition, and month-lock rule must execute deterministically in application code and PostgreSQL transactions—never delegated to an LLM.

To prevent double-spending leave balances under concurrent requests, survive mid-activity worker crashes cleanly, and enforce payroll-grade auditability over locked attendance months, we establish four binding architectural decisions.

---

## Decision

### 1. Double-Entry `leave_ledger` Accounting
- **Append-Only Ledger (`public.leave_ledger`)**:
  - Every mutation to an employee's leave balance (`leave_balances`) **MUST** be accompanied in the **same PostgreSQL transaction** by an immutable `leave_ledger` row recording:
    - `entry_type`: `accrual | carry_forward | reserve | release | deduct | reversal | adjustment`
    - Signed deltas: `delta_allocated`, `delta_reserved`, `delta_used` (`Numeric(6, 2)`)
    - Provenance: `reference_type`, `reference_id`, `note`, `actor_id`
    - Deduplication: tenant-scoped partial unique index on `(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL`.
- **Runtime Immutability**:
  - `agentic_hr_app` is granted `SELECT, INSERT` only on `public.leave_ledger` (`UPDATE`, `DELETE`, and `TRUNCATE` are revoked and blocked by database triggers).
  - Corrections never overwrite historical ledger entries; they append a compensating `reversal` or `adjustment` row.
- **Deterministic Balance Invariant**:
  - For every `(tenant_id, employee_id, leave_type_id, year)` row in `leave_balances`:
    $$\text{available\_days} = \text{allocated\_days} + \text{carried\_forward\_days} - \text{reserved\_days} - \text{used\_days}$$
  - `allocated_days`, `carried_forward_days`, `reserved_days`, and `used_days` carry database `CHECK (... >= 0)` constraints.
- **Upfront vs. Monthly Accrual Initialization**:
  - When an employee is created or imported, `initialize_employee_leave_balances()` initializes `leave_balances` for all active `leave_types` in the current `Asia/Karachi` year:
    - `accrual_mode = 'upfront'` credits `allocated_days = annual_allowance_days` immediately.
    - `accrual_mode = 'monthly'` initializes `allocated_days = 0.00` (with a `0.00` initial `accrual` ledger row) so scheduled monthly accrual jobs never double-allocate.

---

### 2. `SELECT ... FOR UPDATE` Balance Locking
- **Pessimistic Row-Level Locking**:
  - Any operation that checks available leave, reserves pending days, releases a rejected/cancelled reservation, deducts approved days, or applies an HR adjustment **MUST** lock the target `leave_balances` row via `SELECT ... FOR UPDATE` before reading balances or writing `leave_ledger`.
- **Canonical Lock Ordering**:
  - When locking both a `leave_requests` row and a `leave_balances` row (or multiple balance rows during year-end carry-forward), transactions **MUST** acquire locks in canonical order: `leave_requests` first, then `leave_balances` ordered by `id ASC` to eliminate lock-order deadlocks.

---

### 3. Single Idempotent `approve_leave_request_activity` Crash-Recovery Design
- **Atomic Domain Transaction**:
  - Leave approval is executed inside a single database transaction in `approve_leave_request_activity`:
    1. Establish `SET LOCAL app.tenant_id` for Forced RLS.
    2. Lock `leave_requests` row via `SELECT ... FOR UPDATE`.
    3. **Idempotent Early-Return**: If `status == 'approved'`, return the existing outcome immediately without re-deducting balances or duplicating ledger/attendance rows.
    4. Lock `leave_balances` row via `SELECT ... FOR UPDATE`.
    5. Move `reserved_days -> used_days` (or deduct directly for adjustments) and insert a `leave_ledger` row (`entry_type = 'deduct'`) with deterministic `idempotency_key = f"leave-approve:{leave_request_id}"`.
    6. Upsert `attendance` rows (`ON_LEAVE` or `HALF_DAY`, `source = 'leave_approval'`, `leave_request_id = ...`) across the requested working dates (skipping holidays and weekends, and respecting month locks).
    7. Transition `leave_requests.status = 'approved'`, record `decided_by` / `decided_at`, write `audit_logs`, and enqueue `outbox_events` / `sheets_sync_queue`.
    8. `COMMIT`.
- **Pass 2 Separation of External Side Effects**:
  - External Google Calendar event creation, Gmail notifications, and Google Sheets sync execute **after** `approve_leave_request_activity` commits, in separate idempotent Temporal activities / outbox workers. A worker crash before or after commit can never leave `leave_balances`, `leave_ledger`, `attendance`, and `leave_requests` out of sync.

---

### 4. Month-Lock Governance (`attendance.post_lock_edit`)
- **Authoritative Month Lock (`public.attendance_month_locks`)**:
  - Locking a month (`YYYY-MM`) inserts `(tenant_id, year_month)` into `attendance_month_locks` and sets `attendance.locked = true` for all rows in that tenant and month.
- **Direct Edit Prohibition**:
  - Standard manager grid edits, employee check-in/out, Excel attendance imports, and auto-fill jobs **MUST** reject (HTTP `409 Conflict`) any mutation targeting a date in a locked month or an attendance row with `locked = true`.
- **Controlled Exception Path (`attendance.post_lock_edit`)**:
  - Post-lock corrections require a Stage 2 human approval of type `"attendance.post_lock_edit"` (`AttendancePostLockEditPayload`) decided by `owner` or `hr_head`.
  - Upon approval, the correction updates the locked `attendance` row atomically inside the approval transaction, sets `source = 'post_lock_edit'`, preserves `locked = true`, and records before/after diffs in `audit_logs`.

---

## Consequences
- Zero-race leave balance accounting backed by `SELECT ... FOR UPDATE`, partial unique ledger idempotency keys, and non-negative PostgreSQL `CHECK` constraints.
- Crash-safe Temporal leave approval with clean separation between committed PostgreSQL state and external Google Workspace side effects.
- Full payroll-ready auditability over locked monthly attendance sheets while retaining a governed exception path.
