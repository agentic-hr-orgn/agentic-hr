# Stage 1 Notes Proof — Full-Stack Vertical Slice Verification

## 1. Overview & Purpose
This document provides auditable verification evidence that the core architectural foundations of **Agentic HR Core**—PostgreSQL with `pgvector`, FastAPI backend with async SQLAlchemy, generated TypeScript OpenAPI contracts, TanStack Query, and Next.js App Router—operate together end-to-end.

In accordance with **Core Project Rule 1 (Vertical Slice: DB → API → Screen → Test)** and the explicit requirements of Prompt 9:
- All implementation and verification was conducted strictly on a temporary branch (`stage-1/notes-proof`).
- A dedicated disposable PostgreSQL database (`agentic_hr_notes_proof`) was used, preserving the pristine state and migration history of the primary `agentic_hr_db`.
- Only synthetic data was utilized.
- All temporary application code (endpoints, models, pages, and tests) was excluded from the permanent `stage-1/platform-skeleton` branch.

---

## 2. Vertical Slice Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Next.js 14 Frontend                       │
│    apps/web/src/app/(dashboard)/notes/page.tsx                  │
│    - Form inputs (Title, Content)                               │
│    - "Add Note" button [data-testid="add-note-button"]          │
│    - TanStack Query (useQuery + useMutation)                    │
│    - Generated OpenAPI Client (packages/contracts)              │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTP / JSON
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                       FastAPI Backend                           │
│    apps/api/app/routers/notes.py                                │
│    - POST /notes (NoteCreate -> NoteResponse) [201 Created]     │
│    - GET /notes  (list[NoteResponse])                           │
│    - SQLAlchemy 2.0 AsyncSession                                │
└───────────────────────────────┬─────────────────────────────────┘
                                │ asyncpg
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PostgreSQL 16 (Disposable)                    │
│    Database: agentic_hr_notes_proof                             │
│    Table: notes (id, title, content, created_at)                │
└─────────────────────────────────────────────────────────────────┘
                                ▲
                                │ Verification
┌─────────────────────────────────────────────────────────────────┐
│                    Playwright E2E Runner                        │
│    apps/web/e2e/notes.spec.ts                                   │
│    - Navigates to /notes                                        │
│    - Fills title and content                                    │
│    - Clicks Add Note                                            │
│    - Asserts visibility of persisted note in UI                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Database Schema & Isolation

The temporary table was provisioned inside the disposable container database `agentic_hr_notes_proof` without impacting the main database:

```sql
CREATE TABLE public.notes (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);
```

### Table Structure Inspection
```text
                                       Table "public.notes"
   Column   |           Type           | Collation | Nullable |              Default              
------------+--------------------------+-----------+----------+-----------------------------------
 id         | integer                  |           | not null | nextval('notes_id_seq'::regclass)
 title      | character varying(255)   |           | not null | 
 content    | text                     |           | not null | 
 created_at | timestamp with time zone |           | not null | CURRENT_TIMESTAMP
Indexes:
    "notes_pkey" PRIMARY KEY, btree (id)
```

---

## 4. Test Execution & Verification Evidence

### Step 1: Direct Backend API Validation
The FastAPI service was initialized and validated via synthetic HTTP requests:

```powershell
=== Starting FastAPI Backend in Background ===
FastAPI Backend is healthy on port 8000.

=== Testing Direct Backend API (POST /notes & GET /notes) ===
Created Note via API:

id title              content                                            created_at                 
-- -----              -------                                            ----------                 
 2 Synthetic API Note Verified direct FastAPI persistence to PostgreSQL. 2026-11-06T19:53:27.721269Z

Fetched 2 Notes via GET /notes
```

### Step 2: Playwright End-to-End Browser Test
Playwright spun up the production Next.js application, navigated to `/notes`, input synthetic content, triggered the mutation, and asserted DOM updates:

```text
=== Executing Playwright E2E Test (Notes Full-Stack Verification) ===
$ playwright test --grep 'Notes Full-Stack Verification'
[WebServer] $ next start -p 3000

Running 1 test using 1 worker

  ok 1 [chromium] › e2e\notes.spec.ts:4:3 › Notes Full-Stack Verification (stage-1/notes-proof) › creates a new note and verifies it is persisted and displayed (479ms)

  1 passed (2.4s)
```

### Step 3: Direct PostgreSQL Persistence Query
Querying PostgreSQL directly through `docker exec` confirmed that both the direct API record and the Playwright browser-submitted record were stored in the database:

```sql
docker exec -i agentic_hr_db psql -U postgres -d agentic_hr_notes_proof -c "SELECT id, title, content, created_at FROM notes ORDER BY id ASC;"
```

**Output:**
```text
 id |               title                |                                content                                |          created_at           
----+------------------------------------+-----------------------------------------------------------------------+-------------------------------
  1 | Synthetic API Note                 | Verified direct FastAPI persistence to PostgreSQL.                    | 2026-11-06 19:52:02.308748+00
  2 | Synthetic API Note                 | Verified direct FastAPI persistence to PostgreSQL.                    | 2026-11-06 19:53:27.721269+00
  3 | Synthetic E2E Note - 1793994810389 | Verifying full-stack DB -> API -> Screen -> Test slice in PostgreSQL. | 2026-11-06 19:53:30.524843+00
(3 rows)
```

---

## 5. Rollback & Branch Hygiene
Following the verification:
1. This verification document (`docs/stage-1-notes-proof.md`) is preserved as authoritative evidence in `stage-1/platform-skeleton`.
2. The disposable database `agentic_hr_notes_proof` was dropped via `DROP DATABASE IF EXISTS agentic_hr_notes_proof;`.
3. The temporary branch `stage-1/notes-proof` was deleted.
4. No temporary Notes tables, endpoints, pages, or test specs exist on the permanent Stage 1 branch.
