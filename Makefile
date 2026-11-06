# ==============================================================================
# Agentic HR Core - Monorepo Makefile (Cross-Platform / WSL2 / Linux / macOS)
# ==============================================================================

.PHONY: help setup dev seed reset lint test check client db-up db-down api-dev web-dev worker-dev

help:
	@echo "Agentic HR Core - Available Commands:"
	@echo "  make setup     - Install all workspace dependencies (pnpm, uv, pre-commit)"
	@echo "  make dev       - Launch local infrastructure, backend API, worker, and frontend"
	@echo "  make seed      - Safely seed synthetic data (idempotent, Acme Tech Solutions)"
	@echo "  make reset     - Protected reset of local database and development volumes"
	@echo "  make lint      - Lint Python (Ruff) and TypeScript (ESLint)"
	@echo "  make test      - Run automated backend and frontend test suites"
	@echo "  make check     - Complete quality verification gate (lint + test + typecheck)"
	@echo "  make client    - Generate TypeScript client contracts from FastAPI OpenAPI schema"
	@echo "  make db-up     - Start local Docker infrastructure (Postgres, Redis, Temporal)"
	@echo "  make db-down   - Stop local Docker infrastructure"

# 1. Setup: Installs dependencies and configures local environment
setup:
	@echo "==> Setting up environment..."
	@test -f .env || cp .env.example .env
	pnpm install
	uv sync
	@which pre-commit > /dev/null 2>&1 && pre-commit install || true
	@echo "==> Setup completed successfully."

# 2. Database Infrastructure
db-up:
	docker-compose up -d db redis temporal

db-down:
	docker-compose down

# 3. Individual Service Runners
api-dev:
	cd apps/api && uv run uvicorn app.main:app --reload --port 8000

web-dev:
	pnpm --filter @agentic-hr/web dev

worker-dev:
	uv run python apps/api/app/worker.py

# 4. Dev: Starts infrastructure and development processes
dev: db-up
	@echo "==> Starting Agentic HR dev services (API, Worker, Frontend)..."
	@make -j 3 api-dev worker-dev web-dev

# 5. Seed: Safe and idempotent synthetic data injection
seed:
	uv run python apps/api/scripts/seed.py

# 6. Reset: Protected confirmation gate against accidental data loss
reset:
	@echo "WARNING: This will destroy all local database volumes and development data!"
	@test "$(CONFIRM)" = "yes" || test "$(CONFIRM)" = "true" || ( \
		read -p "Are you sure you want to proceed? [y/N]: " ans && [ "$$ans" = "y" -o "$$ans" = "Y" ] \
	) || (echo "Reset cancelled. To force reset, use: make reset CONFIRM=yes" && exit 1)
	docker-compose down -v
	@echo "Local development volumes have been destroyed."

# 7. Lint: Python (Ruff) and TypeScript (ESLint)
lint:
	@echo "==> Linting Python backend (Ruff)..."
	cd apps/api && uv run ruff check app tests
	cd apps/api && uv run ruff format --check app tests
	@echo "==> Linting TypeScript frontend (ESLint)..."
	pnpm --filter @agentic-hr/web run lint

# 8. Test: Automated tests and typechecks
test:
	@echo "==> Running backend Pytest suite..."
	uv run --package agentic-hr-api pytest apps/api/tests/
	@echo "==> Running TypeScript workspace typechecks..."
	pnpm -r typecheck

# 9. Check: Complete CI quality gate
check: lint test
	@echo "==> All quality checks passed successfully!"

# 10. Client: Generate TypeScript contracts from FastAPI OpenAPI schema
client:
	@echo "==> Exporting FastAPI OpenAPI schema..."
	uv run python apps/api/scripts/export_openapi.py
	@echo "==> Generating TypeScript client contracts..."
	pnpm --filter @agentic-hr/contracts run generate
	@echo "==> Verifying generated contracts typecheck..."
	pnpm --filter @agentic-hr/contracts run typecheck
	@echo "==> TypeScript client generation complete."
