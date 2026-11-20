"""
Pydantic Schemas for Google Sheets Synchronization and Drift Management in Agentic HR Core.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.timezones import KarachiDatetime


class SheetsSyncRequest(BaseModel):
    """Payload to trigger immediate synchronization of pending queue or full directory."""

    spreadsheet_id: str | None = Field(
        None, description="Optional Google Spreadsheet ID. Defaults to active tenant sheet."
    )
    sheet_name: str = Field("Employees", description="Target worksheet tab name.")
    entity_type: str = Field("employee", description="Entity collection to sync.")
    batch_size: int = Field(50, ge=1, le=500, description="Max queue rows to process.")


class SheetsSyncResponse(BaseModel):
    """Result of a sync queue execution."""

    status: str
    processed_count: int
    completed_count: int
    failed_count: int
    spreadsheet_id: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class SheetsDriftItem(BaseModel):
    """Detailed discrepancy between PostgreSQL and Google Sheets."""

    entity_id: str
    field_name: str
    postgres_value: str | None
    sheets_value: str | None
    resolution: str


class SheetsReconcileResponse(BaseModel):
    """Result of a drift detection and authoritative reconciliation run."""

    status: str
    spreadsheet_id: str
    scanned_rows: int
    drifts_detected: int
    reconciled_count: int
    drifts: list[SheetsDriftItem] = Field(default_factory=list)


class SheetsDriftLogResponse(BaseModel):
    """Audit log entry for a detected and reconciled drift discrepancy."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    spreadsheet_id: str
    sheet_name: str
    entity_type: str
    entity_id: str
    field_name: str
    postgres_value: str | None
    sheets_value: str | None
    resolution: str
    resolved_by: UUID | None
    created_at: KarachiDatetime
