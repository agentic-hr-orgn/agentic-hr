"""
OpenAPI Schema Exporter for Agentic HR Core.
Extracts OpenAPI definition from FastAPI directly to packages/contracts/openapi.json.
"""

import json
from pathlib import Path

from app.main import app


def export_openapi_schema() -> Path:
    # Resolve project root from apps/api/scripts/export_openapi.py
    current_dir = Path(__file__).resolve().parent
    repo_root = current_dir.parent.parent.parent
    contracts_dir = repo_root / "packages" / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)

    openapi_path = contracts_dir / "openapi.json"
    schema = app.openapi()

    with open(openapi_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)

    print(f"[OpenAPI] Exported schema to {openapi_path}")
    return openapi_path


if __name__ == "__main__":
    export_openapi_schema()
