# Agentic HR Core

**Agentic HR Core** is a multi-tenant agentic AI workforce platform engineered for SMEs, automating high-overhead HR and recruiting workflows with deterministic PostgreSQL authority, least-privilege Google Workspace connectors, and human-in-the-loop governance.

## Platform Status (Stages 1–5 Complete · Next: Stage 6)
- **Stage 1 (Platform Skeleton)**: Monorepo (`Next.js 16` + `FastAPI` + `PostgreSQL 16/pgvector` + `Redis` + `Temporal`) — **Complete**
- **Stage 2 (Governance Core + Pass 1 Hardening)**: Forced Row-Level Security (`FORCE ROW LEVEL SECURITY`), `security_invoker` views (Migration `0013`), immutable `audit_logs`, AES-256-GCM envelope encryption, atomic `IdempotencyGate`, Temporal approval workflows, and 4-tier `ModelGateway` (`cheap`, `strong`, `reasoning`, `private`) — **Complete**
- **Stage 3 (Milestone A: Connectors + Pass 2 Hardening)**: Google Workspace least-privilege connectors (`gmail.send`, `drive.file`, `calendar.events`), Postgres-authoritative one-way Google Sheets sync & drift reconciliation, 13 `@mcp_guardrail` FastMCP tools, durable 401 circuit breaker, and `Asia/Karachi` PKT standardization — **Complete**
- **Stage 4 (Milestone B — Phase 0: Onboarding Wizard, Employees & Encrypted Record Keeper)**: Migrations `0014` & `0015`, 4-field AAD AES-256-GCM PII crypto (CNIC/IBAN), two-phase `.xlsx` bulk importer, resumable 5-step onboarding wizard & PKT launch, cycle-safe org chart, per-employee document vault (`google_drive`/`local`), daily 09:00 PKT 30/7/1-day Temporal expiry scan, and in-app notification bell — **Complete** ([docs/stage-4-verification.md](docs/stage-4-verification.md))
- **Stage 5 (Policy Ingestion & Bilingual Search — RAG Foundation)**: Migration `0016` (`pg_trgm`, 768D HNSW `VECTOR(768)`, GIN `'simple'` FTS, Forced RLS), ADR 008 bilingual Urdu/English normalizer, permissive PDF/DOCX/TXT parser & clause-aware chunker, `ModelGateway.embed_texts` (`gemini-embedding-001`), sandboxed Temporal ingestion, Stage 2 `policy.publish` versioning, hybrid RRF + reranker retrieval (`POST /api/v1/policies/search`), Next.js 16 3-tab Policy Brain UI, and 18-question Gold Set (`Recall@5 >= 90%`) — **Complete** ([docs/stage-5-verification.md](docs/stage-5-verification.md))
- **Next Stage**: **Stage 6 (Leave, Attendance & Payroll Determinism)**

---

## Core AI Agents (4 Specialized Agents)
1. **DocuVerify**: Multi-page OCR & credential validation for scanned/native CVs (Docling OCR).
2. **Screener**: Objective 0–100 rubric candidate scoring and ranking with auditable logs.
3. **Code Tester**: Isolated technical test execution inside firewalled E2B sandboxes.
4. **Policy Brain**: Bilingual (English & Urdu) `pgvector` RAG assistant grounded strictly in approved company handbooks.

---

## Architecture & Repository Layout
- `apps/web/` — Next.js 16 (App Router, React 18/19, TypeScript, Tailwind CSS, Navy `#102C54` / Royal Blue `#2A5BDB` design tokens, Clerk auth, TanStack Query).
- `apps/api/` — FastAPI (`Python 3.13`, `uv`, SQLAlchemy 2.0 `asyncpg`, Alembic, Temporal SDK, FastMCP, `openpyxl`).
- `packages/ui/` — Shared UI component primitives & canonical CSS design tokens (`packages/ui/src/styles/theme.css`).
- `packages/contracts/` — OpenAPI-generated TypeScript types & typed API client (`openapi-typescript`).
- `docs/` — Architecture Decision Records (`adr/001`–`007`), OAuth least-privilege scopes, synthetic dataset kit, and stage verification reports.

---

## Local Quickstart (Windows / Docker)

### 1. Prerequisites
- **Node.js** `24.x` (`pnpm` v12+)
- **Python** `3.13.x` (`uv` v0.13+)
- **Docker** & **Docker Compose**

### 2. Environment Setup
Copy `.env.example` to `.env` (never commit real secrets) and ensure local development defaults are configured:
- `ENVIRONMENT=development` & `USE_FAKE_INTEGRATIONS=true` (strictly forbidden in `production`)
- `DATABASE_URL` (`agentic_hr_app`) and `MIGRATION_DATABASE_URL` (`postgres`)
- `REDIS_URL=redis://localhost:6379/0`
- `TEMPORAL_ADDRESS=localhost:7233`
- `FIELD_ENCRYPTION_KEY` (32-byte base64 KEK for AES-256-GCM)

### 3. Start Databases, Apply Migrations & Seed Synthetic Tenant Data
```powershell
docker-compose up -d db redis temporal
cd apps/api
uv sync
uv run alembic upgrade head
uv run python scripts/seed.py
cd ../..
pnpm install
```

### 4. Run Services (Foreground Terminals)
```powershell
# Terminal 1: FastAPI Backend (http://localhost:8000)
cd apps/api && uv run uvicorn app.main:app --reload --port 8000

# Terminal 2: Temporal & Outbox Worker
cd apps/api && uv run python -m app.worker

# Terminal 3: Next.js 16 Frontend (http://localhost:3000)
pnpm --filter @agentic-hr/web dev
```

---

## Verification & Quality Gate Commands
```powershell
# Backend Lint, Format & Full Pytest Suite
uv run ruff check apps/api ; uv run ruff format --check apps/api
uv run --package agentic-hr-api pytest apps/api/tests -q

# OpenAPI Contract Sync
uv run --package agentic-hr-api python apps/api/scripts/export_openapi.py
pnpm --filter @agentic-hr/contracts generate

# Frontend Typecheck, Lint, Unit Tests, Build & Playwright E2E
pnpm -r typecheck
pnpm --filter @agentic-hr/web lint
pnpm --filter @agentic-hr/web test
pnpm --filter @agentic-hr/web build
pnpm --filter @agentic-hr/web exec playwright test --reporter=list
pnpm format:check
```

---

## Key Documentation & ADRs
- [docs/adr/001-stack.md](docs/adr/001-stack.md) — Platform Stack Decision
- [docs/adr/002-database.md](docs/adr/002-database.md) — Single PostgreSQL 16 + Forced RLS & `pgvector`
- [docs/adr/003-model-routing.md](docs/adr/003-model-routing.md) & [docs/adr/007-model-routing-tiers-2026.md](docs/adr/007-model-routing-tiers-2026.md) — 4-Tier AI Model Routing & Private PII Fail-Closed Boundary
- [docs/adr/005-sheets-view.md](docs/adr/005-sheets-view.md) — PostgreSQL Authoritative Truth vs Google Sheets Synced View
- [docs/adr/006-workspace-workaround.md](docs/adr/006-workspace-workaround.md) & [docs/oauth-scopes.md](docs/oauth-scopes.md) — Least-Privilege Scopes (`gmail.send`, `drive.file`, `calendar.events`) & 6 Authorized Test Accounts
- Verification Reports: [Stage 1](docs/stage-1-verification.md) · [Stage 2](docs/stage-2-verification.md) · [Stage 3](docs/stage-3-verification.md) · [Stage 4](docs/stage-4-verification.md) · [Stage 5](docs/stage-5-verification.md)
