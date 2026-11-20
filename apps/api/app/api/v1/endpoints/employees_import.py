"""
Excel Bulk Employee Importer REST API Endpoints for Agentic HR Core (Stage 4 Prompt 3).

Endpoints:
- GET /api/v1/employees/import/template: Download styled `.xlsx` import template.
- POST /api/v1/employees/import/validate: Validate `.xlsx` rows and stage clean/failed batch.
- GET /api/v1/employees/import/{batch_id}/error-report: Download `.xlsx` error report.
- POST /api/v1/employees/import/{batch_id}/commit: Two-pass commit of validated batch.
"""

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUserContext, require_owner_manager_or_hr_head
from app.db.session import get_db
from app.schemas.employee_import import ImportCommitResponse, ImportValidationResult
from app.services.employee_import_service import EmployeeImportService

router = APIRouter()

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/template")
async def download_employee_import_template(
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Generates and returns a tenant-customized `.xlsx` employee import template."""
    xlsx_bytes = await EmployeeImportService.generate_template_xlsx(
        session=db,
        tenant_id=current_user.tenant_id,
    )
    return Response(
        content=xlsx_bytes,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="employee_import_template.xlsx"',
        },
    )


@router.post("/validate", response_model=ImportValidationResult)
async def validate_employee_import_xlsx(
    file: UploadFile = File(...),
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> ImportValidationResult:
    """
    Parses and validates every row in an uploaded `.xlsx` workbook without stopping on first error,
    stages encrypted PII in Redis (30-min TTL), and returns masked preview rows + per-row errors.
    """
    content = await file.read()
    return await EmployeeImportService.validate_upload_xlsx(
        session=db,
        tenant_id=current_user.tenant_id,
        file_bytes=content,
        filename=file.filename or "employees.xlsx",
    )


@router.get("/{batch_id}/error-report")
async def download_employee_import_error_report(
    batch_id: str,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
) -> Response:
    """Returns a downloadable `.xlsx` highlighting invalid rows and their validation errors."""
    xlsx_bytes = await EmployeeImportService.download_error_report_xlsx(
        tenant_id=current_user.tenant_id,
        import_batch_id=batch_id,
    )
    safe_batch = "".join(ch for ch in batch_id if ch.isalnum())[:24] or "batch"
    return Response(
        content=xlsx_bytes,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="employee_import_errors_{safe_batch}.xlsx"'
            ),
        },
    )


@router.post("/{batch_id}/commit", response_model=ImportCommitResponse)
async def commit_employee_import_batch(
    batch_id: str,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> ImportCommitResponse:
    """
    Commits a clean validated import batch (`invalid_count == 0`) using a two-pass insert
    (Pass 1: insert employees with AES-256-GCM 4-field AAD PII; Pass 2: link manager_id),
    enqueues `sheets_sync_queue`, writes `audit_logs`, and clears the staging key.
    """
    return await EmployeeImportService.commit_import_batch(
        session=db,
        tenant_id=current_user.tenant_id,
        import_batch_id=batch_id,
        actor_id=current_user.user_id,
    )
