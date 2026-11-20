"""
Pydantic Schemas for Stage 4 Excel Bulk Employee Importer (Validation, Error Report & Two-Phase Commit).
"""

from pydantic import BaseModel, Field


class ImportRowError(BaseModel):
    """Single validation failure on a specific row and column."""

    row_number: int
    field: str
    message: str


class ImportPreviewRow(BaseModel):
    """
    Sanitized preview of an uploaded Excel row.
    Exposes only masked CNIC and masked bank account values (never plaintext PII).
    """

    row_number: int
    full_name: str
    email: str
    phone: str | None = None
    department: str
    manager_email: str | None = None
    job_title: str
    employment_type: str
    join_date: str
    cnic_masked: str | None = None
    bank_account_masked: str | None = None
    is_valid: bool
    errors: list[str] = Field(default_factory=list)


class ImportValidationResult(BaseModel):
    """Deterministic validation result returned by POST /api/v1/employees/import/validate."""

    import_batch_id: str
    file_name: str
    total_rows: int
    valid_count: int
    invalid_count: int
    can_commit: bool
    preview_rows: list[ImportPreviewRow]
    errors: list[ImportRowError]


class ImportCommitResponse(BaseModel):
    """Response returned by POST /api/v1/employees/import/{batch_id}/commit."""

    status: str
    import_batch_id: str
    committed_count: int
    sheets_queued: int
    employee_ids: list[str]
