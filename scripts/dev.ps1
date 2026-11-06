# ==============================================================================
# Agentic HR Core - Windows PowerShell Helper Script
# Provides 100% feature parity with the project Makefile for native Windows users
# ==============================================================================

param (
    [Parameter(Position=0)]
    [ValidateSet("setup", "dev", "seed", "reset", "lint", "test", "check", "client", "db-up", "db-down", "api", "web", "worker", "help")]
    [string]$Command = "help",

    [Parameter()]
    [switch]$Force = $false
)

$RootDir = Split-Path -Parent $PSScriptRoot

switch ($Command) {
    "setup" {
        Write-Host "==> Setting up local environment..." -ForegroundColor Cyan
        Set-Location $RootDir
        if (-not (Test-Path ".env")) {
            Copy-Item ".env.example" ".env"
            Write-Host "Created .env from .env.example" -ForegroundColor Green
        }
        Write-Host "Installing pnpm dependencies..." -ForegroundColor Cyan
        pnpm install
        Write-Host "Syncing Python dependencies..." -ForegroundColor Cyan
        uv sync
        Write-Host "==> Setup completed successfully." -ForegroundColor Green
    }

    "db-up" {
        Write-Host "Starting Docker services (Postgres + Redis + Temporal)..." -ForegroundColor Cyan
        Set-Location $RootDir
        docker-compose up -d db redis temporal
    }

    "db-down" {
        Write-Host "Stopping Docker services..." -ForegroundColor Cyan
        Set-Location $RootDir
        docker-compose down
    }

    "api" {
        Write-Host "Starting FastAPI backend on http://localhost:8000..." -ForegroundColor Cyan
        Set-Location "$RootDir\apps\api"
        uv run uvicorn app.main:app --reload --port 8000
    }

    "web" {
        Write-Host "Starting Next.js frontend on http://localhost:3000..." -ForegroundColor Cyan
        Set-Location $RootDir
        pnpm --filter @agentic-hr/web dev
    }

    "worker" {
        Write-Host "Starting Temporal background worker..." -ForegroundColor Cyan
        Set-Location $RootDir
        uv run python apps/api/app/worker.py
    }

    "dev" {
        Write-Host "==========================================================" -ForegroundColor Blue
        Write-Host "   Agentic HR Core - Local Development Stack              " -ForegroundColor Blue
        Write-Host "==========================================================" -ForegroundColor Blue
        Write-Host "1. Starting Docker containers..." -ForegroundColor Cyan
        Set-Location $RootDir
        docker-compose up -d db redis temporal

        Write-Host "`nTo run the local services in separate terminals:" -ForegroundColor Yellow
        Write-Host "  Terminal 1 (API):    powershell -File scripts/dev.ps1 api" -ForegroundColor White
        Write-Host "  Terminal 2 (Worker): powershell -File scripts/dev.ps1 worker" -ForegroundColor White
        Write-Host "  Terminal 3 (Web):    powershell -File scripts/dev.ps1 web" -ForegroundColor White
        Write-Host "  Temporal Web UI:     http://localhost:8233" -ForegroundColor White
        Write-Host "  FastAPI Swagger UI:  http://localhost:8000/docs" -ForegroundColor White
        Write-Host "  Next.js Frontend:    http://localhost:3000`n" -ForegroundColor White
    }

    "seed" {
        Write-Host "==> Seeding synthetic data (Acme Tech Solutions)..." -ForegroundColor Cyan
        Set-Location $RootDir
        uv run python apps/api/scripts/seed.py
    }

    "reset" {
        Write-Host "WARNING: This will destroy all local database volumes and development data!" -ForegroundColor Red
        if (-not $Force) {
            $confirmation = Read-Host "Are you sure you want to proceed? [y/N]"
            if ($confirmation -ne "y" -and $confirmation -ne "Y") {
                Write-Host "Reset cancelled. To force reset without prompting, run: powershell -File scripts/dev.ps1 reset -Force" -ForegroundColor Yellow
                return
            }
        }
        Set-Location $RootDir
        docker-compose down -v
        Write-Host "Local development volumes have been destroyed." -ForegroundColor Green
    }

    "lint" {
        Write-Host "==> Linting Python backend (Ruff)..." -ForegroundColor Cyan
        Set-Location "$RootDir\apps\api"
        uv run ruff check app tests
        uv run ruff format --check app tests
        Write-Host "==> Linting TypeScript frontend (ESLint)..." -ForegroundColor Cyan
        Set-Location $RootDir
        pnpm --filter @agentic-hr/web run lint
    }

    "test" {
        Write-Host "==> Running Pytest suite..." -ForegroundColor Cyan
        Set-Location $RootDir
        uv run --package agentic-hr-api pytest apps/api/tests/
        Write-Host "==> Running TypeScript workspace typechecks..." -ForegroundColor Cyan
        pnpm -r typecheck
    }

    "check" {
        Write-Host "==========================================================" -ForegroundColor Cyan
        Write-Host "   Agentic HR Core - Full Quality Verification Gate       " -ForegroundColor Cyan
        Write-Host "==========================================================" -ForegroundColor Cyan
        Write-Host "1/4. Linting Python backend (Ruff)..." -ForegroundColor Cyan
        Set-Location "$RootDir\apps\api"
        uv run ruff check app tests
        uv run ruff format --check app tests

        Write-Host "2/4. Running backend tests (Pytest)..." -ForegroundColor Cyan
        Set-Location $RootDir
        uv run --package agentic-hr-api pytest apps/api/tests/

        Write-Host "3/4. Typechecking all workspace packages..." -ForegroundColor Cyan
        pnpm -r typecheck

        Write-Host "4/4. Linting Next.js frontend (ESLint)..." -ForegroundColor Cyan
        pnpm --filter @agentic-hr/web run lint

        Write-Host "`n==> ALL QUALITY CHECKS PASSED!" -ForegroundColor Green
    }

    "client" {
        Write-Host "==> Exporting FastAPI OpenAPI schema..." -ForegroundColor Cyan
        Set-Location $RootDir
        uv run python apps/api/scripts/export_openapi.py
        Write-Host "==> Generating TypeScript client contracts..." -ForegroundColor Cyan
        pnpm --filter @agentic-hr/contracts run generate
        Write-Host "==> Verifying contracts typecheck..." -ForegroundColor Cyan
        pnpm --filter @agentic-hr/contracts run typecheck
        Write-Host "==> TypeScript client generation complete." -ForegroundColor Green
    }

    "help" {
        Write-Host "Agentic HR Core - Available Commands:" -ForegroundColor Cyan
        Write-Host "  powershell -File scripts/dev.ps1 setup     - Install all workspace dependencies"
        Write-Host "  powershell -File scripts/dev.ps1 dev       - Start local infrastructure and view dev stack commands"
        Write-Host "  powershell -File scripts/dev.ps1 seed      - Safely seed synthetic data (idempotent)"
        Write-Host "  powershell -File scripts/dev.ps1 reset     - Protected reset of local volumes (-Force to bypass prompt)"
        Write-Host "  powershell -File scripts/dev.ps1 lint      - Run Ruff and ESLint"
        Write-Host "  powershell -File scripts/dev.ps1 test      - Run Pytest and TypeScript typechecks"
        Write-Host "  powershell -File scripts/dev.ps1 check     - Run full quality verification gate"
        Write-Host "  powershell -File scripts/dev.ps1 client    - Generate TypeScript client from FastAPI OpenAPI schema"
        Write-Host "  powershell -File scripts/dev.ps1 db-up     - Start Docker services"
        Write-Host "  powershell -File scripts/dev.ps1 db-down   - Stop Docker services"
    }
}
