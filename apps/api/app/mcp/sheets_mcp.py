"""
Google Sheets FastMCP Server for Agentic HR Core.
Exposes sync_sheets_tool, read_sheets_range_tool, and reconcile_sheets_drift_tool
under ADR 005 (PostgreSQL authoritative source of truth) and ADR 006 (drive.file scope).
"""

from typing import Any

from app.mcp.base import FastMCP
from app.mcp.context import get_current_mcp_context, get_mcp_session
from app.mcp.guardrails import mcp_guardrail
from app.services.sheets_sync_service import SheetsSyncService

sheets_mcp_server = FastMCP(
    name="agentic-hr-sheets",
    instructions="Google Sheets connector tools for syncing employee directories and reconciling drift against PostgreSQL authoritative state.",
)


@sheets_mcp_server.tool(
    name="sync_sheets_tool",
    description="Syncs rows to Google Sheets (append or update mode). Enforces read-only check, rate limit, and token refresh.",
)
@mcp_guardrail("sync_sheets_tool", is_write=True)
async def sync_sheets_tool(
    spreadsheet_id: str | None = None,
    sheet_name: str = "Employees",
    rows: list[list[Any]] | None = None,
    mode: str = "append",
    range_name: str | None = None,
) -> dict[str, Any]:
    """
    Appends or updates employee directory rows in the tenant's synced Google Sheet.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        response = await SheetsSyncService.sync_rows(
            session=session,
            tenant_id=ctx.tenant_id,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            rows=rows,
            mode=mode,
            range_name=range_name,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            "status": response.status,
            "spreadsheet_id": response.spreadsheet_id,
            "processed_items": getattr(
                response, "processed_count", getattr(response, "processed_items", 0)
            ),
            "processed_count": getattr(
                response, "processed_count", getattr(response, "processed_items", 0)
            ),
            "failed_items": getattr(response, "failed_count", getattr(response, "failed_items", 0)),
            "failed_count": getattr(response, "failed_count", getattr(response, "failed_items", 0)),
            "details": response.details,
        }


@sheets_mcp_server.tool(
    name="read_sheets_range_tool",
    description="Reads cell values from a designated spreadsheet range under drive.file scope. Read-only operation permitted for all authorized tenant roles.",
)
@mcp_guardrail("read_sheets_range_tool", is_write=False)
async def read_sheets_range_tool(
    spreadsheet_id: str,
    range_name: str,
) -> list[list[Any]]:
    """
    Reads rows from the specified spreadsheet and range.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        rows = await SheetsSyncService.read_range(
            session=session,
            tenant_id=ctx.tenant_id,
            spreadsheet_id=spreadsheet_id,
            range_name=range_name,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return rows


@sheets_mcp_server.tool(
    name="reconcile_sheets_drift_tool",
    description="Scans Google Sheets for human tampering/drift, logs discrepancies, and overwrites drifted cells with PostgreSQL authoritative truth.",
)
@mcp_guardrail("reconcile_sheets_drift_tool", is_write=True)
async def reconcile_sheets_drift_tool(
    spreadsheet_id: str | None = None,
    sheet_name: str = "Employees",
) -> dict[str, Any]:
    """
    Reconciles Google Sheets against PostgreSQL authoritative truth.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        response = await SheetsSyncService.reconcile_drift(
            session=session,
            tenant_id=ctx.tenant_id,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            "status": response.status,
            "spreadsheet_id": response.spreadsheet_id,
            "scanned_rows": response.scanned_rows,
            "drifts_detected": response.drifts_detected,
            "reconciled_count": response.reconciled_count,
            "drifts": [
                {
                    "entity_type": getattr(d, "entity_type", "employee"),
                    "entity_id": d.entity_id,
                    "field_name": d.field_name,
                    "postgres_value": d.postgres_value,
                    "sheets_value": d.sheets_value,
                    "resolution": getattr(d, "resolution", "overwritten_from_postgres"),
                    "row_index": getattr(d, "row_index", None),
                }
                for d in response.drifts
            ],
        }
