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
3. Never introduce patterns or dependencies that violate ADR decisions (ADR 001 through 006).
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

## 6. AI Model Routing & Privacy Policy (ADR 003)

### Model Routing Matrix (100% Free Tier via Google AI Studio)
- **Strong Model**: `gemini-3.8-flash` (15 RPM / 1,500 RPD, 1M context) — Policy Brain & complex reasoning.
- **Cheap Model**: `gemini-3.5-flash-lite` (30 RPM / 1,500 RPD, 1M context) — High-throughput document ingestion & candidate screening.
- **Multilingual Embedding**: `gemini-embedding-2` (100 RPM / 1,500 RPD, MRL dimensions: 768/1536/3072) — Urdu + English bilingual policy RAG.

### Free-Tier No-Training Privacy Boundary
- Content sent through the Google AI Studio Free Tier is subject to human review and training to improve Google products.
- **Mandatory Policy**: Only **synthetic test data** (Acme Tech Solutions) is permitted on free-tier APIs.
- Real candidate or employee data must NEVER touch free-tier endpoints. Real pilot and production workloads must route through enterprise-grade paid endpoints (e.g., Vertex AI) with zero-training confidentiality guarantees.

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
│   ├── adr/                   # Architecture Decision Records (001 - 006)
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
│       ├── app/
│       │   ├── main.py        # FastAPI entry point
│       │   ├── core/          # Config, database, security
│       │   ├── integrations/  # Fake (Stage 1) vs Real Google service adapters
│       │   ├── models/        # TenantBase & synthetic schemas
│       │   ├── routers/       # /api/health, /api/synthetic
│       │   └── services/      # Synthetic data service
│       └── tests/             # Pytest integration suite
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

### Unified Validation (Cross-Platform)
```powershell
# Windows PowerShell (Native)
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 check

# Linux / macOS / WSL
make check
```

### Local Services
```powershell
# Start PostgreSQL container
docker-compose up -d db

# Run Backend (Terminal 1)
cd apps/api && uv run uvicorn app.main:app --reload --port 8000

# Run Frontend (Terminal 2)
pnpm --filter @agentic-hr/web dev
```

---

## 11. Current Stage: Stage 1 — Platform Skeleton
- Backend and frontend skeletons are fully established.
- Fake integration adapters are active (`USE_FAKE_INTEGRATIONS=true`).
- 8/8 automated backend tests pass.
- Frontend compiles cleanly under Next.js 16 with zero ESLint/TypeScript errors.
- Next steps proceed into Stage 2 (Database Migrations & Tenant Isolation Verification).
