# AGENTS.md — Permanent Antigravity Project Instructions

## 1. Project Purpose & Architecture
**Agentic HR Core** is an agentic AI workforce platform engineered specifically for SMEs. It automates high-overhead HR and recruiting workflows using four specialized autonomous AI agents:
1. **DocuVerify**: Multi-page document OCR and credential validation for scanned and native PDF CVs using Docling OCR.
2. **Screener**: Objective 0-100 rubric-based scoring and ranking of candidate profiles with auditable explanation logs.
3. **Code Tester**: Isolated candidate technical assessment and test suite execution inside firewalled E2B sandboxes.
4. **Policy Brain**: Bilingual (English & Urdu) Retrieval-Augmented Generation (RAG) assistant for corporate policies, leaves, and attendance.

### System Architecture
The application is built on a decoupled, multi-tenant vertical-slice architecture:
- **Frontend**: Next.js 16 (Active LTS) with TypeScript, Tailwind CSS, custom navy/royal-blue design tokens (`src/styles/theme.css`), and Clerk authentication.
- **Backend API**: FastAPI (Python 3.13 + `uv`) providing async endpoints, LiteLLM gateway, Langfuse tracing, and Sentry error monitoring.
- **Database**: Single PostgreSQL 16 database with `pgvector` for vector embeddings and Row-Level Security (RLS) for tenant isolation.
- **Workflow Orchestration**: Temporal Cloud / local worker for durable multi-day workflows (such as Sheets synchronization and approval gates).
- **Integration Boundary**: Google Workspace least-privilege connectors with in-memory fake adapters (`USE_FAKE_INTEGRATIONS=true`).

---

## 2. Technology Stack & Runtime Environment
- **Node.js**: `v24.21.0` (Active LTS)
- **Package Manager (Frontend)**: `pnpm` (v12+)
- **Frontend Framework**: Next.js 16 (Active LTS) with React 18/19
- **Python**: `3.13.0`
- **Package Manager (Backend)**: `uv` (v0.13+)
- **Backend Framework**: FastAPI (v0.115+) with `pydantic-settings` (v2+)
- **Database & ORM**: PostgreSQL 16 + `pgvector`, SQLAlchemy 2.0 (asyncio), `asyncpg`
- **Containers**: Docker `29.9+`, Docker Compose `v5.6+` (`docker-compose`)
- **Authentication**: Clerk (`@clerk/nextjs` / JWT validation)
- **Testing & Quality**: `pytest`, `pytest-asyncio`, `ruff` (backend); `tsc`, `next lint` (frontend)

---

## 3. Mandatory Agent Workflow & Hierarchy
Before writing code, altering configuration, or proposing architectural changes, future coding agents **MUST**:
1. Read `CLAUDE.md` and this `AGENTS.md` file.
2. Read all existing ADR documents in `docs/adr/`.
3. Never introduce patterns or dependencies that violate ADR decisions (ADR 001 through 007).
4. Query user permission before altering host environments or launching standalone background processes.
5. In planning mode (`/plan`), create a detailed implementation plan artifact and obtain explicit user approval before modifying code or files.

---

## 4. The Seven Core Project Rules (from CLAUDE.md)
1. **Vertical slice**: Build one feature DB -> API -> Screen -> Test end-to-end.
2. **Spec before code**: 10 lines defining inputs, outputs, business rules, failure modes, and tests before writing code.
3. **LLM proposes, code decides**: Critical business logic (payroll calculations, statutory leave balances, approval transitions) must reside in tested, deterministic application code—never delegated blindly to an LLM.
4. **Fake mode first**: `USE_FAKE_INTEGRATIONS=true` must be supported and tested before connecting live external services.
5. **Exit gate**: Every feature must pass automated tests, maintain audit logs, support synthetic seed data, emit metrics, and include documentation notes before completion.
6. **Untrusted data**: Candidate CVs, email bodies, and external files are untrusted data. They must never be allowed to pass executable instructions to AI models (Prompt Injection Defense).
7. **No secrets in git**: Use synthetic data and environment variables exclusively. Never commit API keys or production credentials.

---

## 5. Security & Integration Principles (ADR 006 & OAuth Scopes)

### Google Workspace Least Privilege (docs/oauth-scopes.md)
Only the following minimum scopes are permitted:
- **Gmail**: `gmail.send` (Send-only for notification and forwarding).
- **Drive**: `drive.file` (Per-file access for CV uploads created/opened by the app).
- **Calendar**: `calendar.events` (Interview scheduling).
- **Sheets**: Synced view via `drive.file`.

**Strictly Prohibited Scopes**:
- `gmail.readonly` (NEVER request or introduce inbox-reading access).
- `gmail.modify`
- `mail.google.com`

### Temporary V1 Testing Account Setup (ADR 006)
- Six dedicated test accounts are authorized under Google Auth Platform:
  1. `agentic.hr.test.ubaid.owner@gmail.com`
  2. `agentic.hr.test.ubaid.recruiter@gmail.com`
  3. `agentic.hr.test.ubaid.hm@gmail.com`
  4. `agentic.hr.test.ubaid.employee@gmail.com`
  5. `agentic.hr.test.ubaid.candidate1@gmail.com`
  6. `agentic.hr.test.ubaid.candidate2@gmail.com`
- Do not assume a managed Google Workspace domain or Admin Directory API is available.

---

## 6. AI Model Routing & Privacy Policy (ADR 003 & ADR 007)

### Four-Tier Model Routing Matrix (Environment-Configured via `ModelGateway`)
- **`cheap`** (`CHEAP_MODEL_PROVIDER` / `CHEAP_MODEL_ID`): High-throughput OCR structuring & bulk candidate screening (e.g., `gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gemini-4-hydrogen`).
- **`strong`** (`STRONG_MODEL_PROVIDER` / `STRONG_MODEL_ID`): Bilingual (English & Urdu) Policy Brain RAG & structured rubric analysis (e.g., `gemini-3.8-flash`, `claude-sonnet-5-5`, `gemini-4-argon`).
- **`reasoning`** (`REASONING_MODEL_PROVIDER` / `REASONING_MODEL_ID`): Deep multi-step compliance & statutory edge-case critique (e.g., `claude-opus-5-5`).
- **`private`** (`PRIVATE_MODEL_PROVIDER` / `PRIVATE_MODEL_ID` / `PRIVATE_MODEL_ENDPOINT`): **Fail-closed** VPC/private endpoint — mandatory for all real (non-synthetic) sensitive HR PII (CNICs, IBANs, compensation). Never falls back to public or free-tier routes.
- **Multilingual Embedding**: `gemini-embedding-2` (MRL dimensions: 768/1536/3072) — Urdu + English bilingual policy RAG.

### Privacy & No-Training Boundary
- Content sent through free-tier developer endpoints (e.g. Google AI Studio Free Tier) is subject to human review and training.
- **Mandatory Policy**: Only **synthetic test data** (Acme Tech Solutions) is permitted on free-tier APIs.
- Real (non-synthetic) PII is strictly restricted to the `private` route; `cheap`, `strong`, and `reasoning` routes are permitted only for synthetic data or zero-retention enterprise contracts.

---

## 7. Database & Multi-Tenant Isolation (ADR 002 & ADR 005)
- **One Postgres Database**: All tenants share a single PostgreSQL database with Row-Level Security (RLS).
- **Tenant Key**: Every entity table must inherit `tenant_id` from `TenantBase` (`api/app/models/tenant.py`) and index it.
- **Postgres is Authoritative**: PostgreSQL is the single source of truth. Google Sheets is strictly a **synced view** or designated import/export format synchronized via background workflows—never an authoritative database.

---

## 8. Repository Structure
```
agentic-hr/
├── AGENTS.md                  # Permanent Antigravity instructions (this file)
├── CLAUDE.md                  # Development rules and shortcuts
├── README.md                  # Platform overview
├── .env                       # Local secrets (git-ignored)
├── .env.example               # Environment template
├── .gitignore                 # Git ignore rules
├── docker-compose.yml         # Postgres 16 + pgvector container
├── Makefile                   # make check, make dev, make db-up
├── package.json               # Root pnpm monorepo workspace manifest
├── pnpm-workspace.yaml        # Declares apps/web, packages/ui, packages/contracts
├── pyproject.toml             # Root uv Python workspace manifest
├── scripts/
│   └── dev.ps1                # PowerShell task runner for Windows
├── .github/
│   └── workflows/
│       └── ci.yml             # Monorepo CI workflow
├── docs/
│   ├── adr/                   # Architecture Decision Records (001 - 007)
│   ├── oauth-scopes.md        # OAuth scope whitelist & CV ingestion policy
│   └── synthetic-data-plan.md # Acme Tech Solutions synthetic dataset kit
├── apps/
│   ├── web/                   # Next.js 16 Frontend App (Active LTS)
│   │   ├── package.json       # @agentic-hr/web
│   │   ├── tsconfig.json
│   │   ├── next.config.mjs
│   │   ├── tailwind.config.ts
│   │   └── src/app/           # App Router layout & pages
│   └── api/                   # FastAPI Backend Service (Python 3.13 + uv)
│       ├── pyproject.toml     # Member pyproject.toml
│       ├── alembic/versions/  # Migrations 0001 - 0013 (RLS + security_invoker views)
│       ├── app/
│       │   ├── main.py        # FastAPI entry point
│       │   ├── worker.py      # Temporal + OutboxPublisherWorker entrypoint
│       │   ├── core/          # Config, encryption, idempotency, model_gateway, timezones, security
│       │   ├── integrations/  # Fake vs Real Google service adapters
│       │   ├── mcp/           # 13 FastMCP tools & @mcp_guardrail
│       │   ├── models/        # TenantBase & domain schemas
│       │   ├── api/v1/        # Versioned REST endpoints (/approvals, /connectors, /auth)
│       │   ├── services/      # Connector wrapper, Gmail, Sheets, Drive, Calendar, Outbox
│       │   └── workflows/     # Durable Temporal workflows & activities
│       └── tests/             # Pytest integration & security regression suite
└── packages/
    ├── ui/                    # Shared Component & Design System Package
    │   ├── package.json       # @agentic-hr/ui
    │   ├── tsconfig.json
    │   └── src/
    │       ├── components/    # Button, Card, Badge
    │       └── styles/
    │           └── theme.css  # Navy Blue #102C54, Royal Blue #2A5BDB
    └── contracts/             # Shared TypeScript Contracts & Schemas
        ├── package.json       # @agentic-hr/contracts
        ├── tsconfig.json
        └── src/
            └── index.ts       # Shared DTOs, TenantContext, Employee, Health schemas
```

---

## 9. Coding Standards & Naming Conventions
- **Language Standards**: TypeScript strict mode for frontend; Python 3.13 typed annotations (`X | None`, `list[T]`, `dict[K, V]`) for backend.
- **Linting & Formatting**:
  - Python: `ruff check` and `ruff format` enforced (100 char line limit).
  - TypeScript: ESLint (`next/core-web-vitals`) with zero warnings or errors.
- **Git Branching & Commits**:
  - Branches: `feat/<name>` or `fix/<name>`
  - Commits: Conventional commits: `feat(scope): message` or `fix(scope): message`.
- **UI Design System**: All UI components must use tokens defined in `src/styles/theme.css` (Navy Blue `#102C54`, Royal Blue `#2A5BDB`, and `#F8F9FD` background).

---

## 10. Development & Testing Commands

### Windows Foreground Verification Suite (Never Background)
```powershell
# Python Backend Lint & Format
uv run ruff check apps/api ; uv run ruff format --check apps/api

# Full Backend Pytest Suite (requires Postgres 5432, Redis 6379, Temporal 7233)
uv run --package agentic-hr-api pytest apps/api/tests -q

# Frontend Typecheck, Lint, Unit Tests & Playwright Smoke
pnpm --filter @agentic-hr/contracts typecheck
pnpm --filter @agentic-hr/ui typecheck
pnpm --filter @agentic-hr/web typecheck
pnpm --filter @agentic-hr/web lint
pnpm --filter @agentic-hr/web test
pnpm --filter @agentic-hr/web exec playwright test --reporter=list
pnpm format:check
```

### Local Services
```powershell
# Start PostgreSQL, Redis, Temporal containers
docker-compose up -d db redis temporal

# Run Backend (Terminal 1)
cd apps/api && uv run uvicorn app.main:app --reload --port 8000

# Run Frontend (Terminal 2)
pnpm --filter @agentic-hr/web dev
```

---

## 11. Current Stage: Stages 1–5 Complete — Ready for Stage 6
- **Stage 1 (Platform Skeleton)**: Monorepo, Next.js 16, FastAPI, Docker Postgres/Redis/Temporal complete.
- **Stage 2 (Governance Core + Pass 1 Hardening)**: Forced RLS, Migration `0013` (`security_invoker` alias views, `audit_logs` TRUNCATE trigger, `outbox_due_tenant_ids`), atomic `IdempotencyGate`, AES-256-GCM envelope encryption, Temporal approvals, ADR 007 4-tier `ModelGateway`.
- **Stage 3 (Milestone A Connectors + Pass 2 Hardening)**: Google Workspace connectors (Gmail, Sheets, Drive, Calendar), 13 `@mcp_guardrail` FastMCP tools, durable 401 circuit-breaker persistence across HTTP & MCP, `Asia/Karachi` timezone standardization (`app.core.timezones`), and enterprise Live/Standby Next.js 16 UI.
- **Stage 4 (Milestone B — Phase 0: Onboarding Wizard, Employees & Encrypted Record Keeper)**: Migrations `0014` & `0015` (6 forced-RLS tables, `pub_employees` & `pub_documents_expiring` secure views, `document_scan_tenant_ids`), 4-field AAD AES-256-GCM PII crypto, two-phase `.xlsx` importer, resumable 5-step onboarding wizard & PKT launch, cycle-safe org chart, per-employee document vault (`google_drive`/`local`), daily PKT 30/7/1-day Temporal expiry scan & notification bell (`v0.4-stage4-hardened`, `docs/stage-4-verification.md`).
- **Stage 5 (Policy Ingestion & Bilingual Search — RAG Foundation)**: Migration `0016` (`pg_trgm`, 768D HNSW `VECTOR(768)`, GIN `'simple'` FTS, Forced RLS), ADR 008 bilingual Urdu/English normalizer, permissive PDF/DOCX/TXT parser & clause-aware chunker, `ModelGateway.embed_texts` (`gemini-embedding-001`), sandboxed Temporal ingestion, Stage 2 `policy.publish` versioning, hybrid RRF + reranker retrieval (`POST /api/v1/policies/search`), Next.js 16 3-tab Policy Brain UI, and 18-question Gold Set (`Recall@5 >= 90%`) (`v0.5-stage5-rag-verified`, `docs/stage-5-verification.md`).
- **Next Stage**: **Stage 6 (Leave, Attendance & Payroll Determinism)**.
