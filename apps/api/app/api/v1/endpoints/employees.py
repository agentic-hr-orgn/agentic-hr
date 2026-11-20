"""
Employee Directory, Role-Based Profiles, Org Chart, and Per-Employee Document Vault
REST API Endpoints for Agentic HR Core (Stage 4 Prompt 4).

Endpoints:
- GET    /api/v1/employees
- POST   /api/v1/employees
- GET    /api/v1/employees/org-chart
- GET    /api/v1/employees/{employee_id}
- PATCH  /api/v1/employees/{employee_id}
- GET    /api/v1/employees/{employee_id}/documents
- POST   /api/v1/employees/{employee_id}/documents
- GET    /api/v1/employees/{employee_id}/documents/{document_id}/download
- DELETE /api/v1/employees/{employee_id}/documents/{document_id}
"""

from datetime import date
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
    require_owner_manager_or_hr_head,
)
from app.db.session import get_db
from app.schemas.employee import (
    EmployeeCreateRequest,
    EmployeeDocumentResponse,
    EmployeeListResponse,
    EmployeeProfileResponse,
    EmployeeUpdateRequest,
    OrgChartResponse,
)
from app.services.employee_document_service import EmployeeDocumentService
from app.services.employee_service import EmployeeService
from app.services.storage_helper import sanitize_filename

router = APIRouter()


@router.get("", response_model=EmployeeListResponse)
async def list_employees(
    search: str | None = Query(None, max_length=200),
    department_id: UUID | None = Query(None),
    employee_status: str | None = Query(None, alias="status"),
    employment_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmployeeListResponse:
    """
    Paginated employee directory queried strictly from `public.pub_employees`.
    Never selects or returns encrypted CNIC or bank account fields.
    """
    return await EmployeeService.list_employees(
        session=db,
        tenant_id=current_user.tenant_id,
        search=search,
        department_id=department_id,
        status_filter=employee_status,
        employment_type=employment_type,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=EmployeeProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreateRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> EmployeeProfileResponse:
    """
    Creates an employee record in the caller's tenant.
    CNIC and bank account fields are restricted to owner, hr_head, and payroll_admin.
    """
    result = await EmployeeService.create_employee(
        session=db,
        tenant_id=current_user.tenant_id,
        payload=payload,
        actor=current_user,
    )
    await db.commit()
    return result


@router.get("/org-chart", response_model=OrgChartResponse)
async def get_org_chart(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgChartResponse:
    """Returns a cycle-safe hierarchical org chart built from `public.pub_employees`."""
    return await EmployeeService.get_org_chart(
        session=db,
        tenant_id=current_user.tenant_id,
    )


@router.get("/{employee_id}", response_model=EmployeeProfileResponse)
async def get_employee_profile(
    employee_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmployeeProfileResponse:
    """
    Returns full employee profile with strict role-based encrypted PII decryption.
    Commits `employee.sensitive_viewed` audit log whenever encrypted PII is decrypted.
    """
    result = await EmployeeService.get_employee_profile(
        session=db,
        tenant_id=current_user.tenant_id,
        employee_id=employee_id,
        actor=current_user,
    )
    await db.commit()
    return result


@router.patch("/{employee_id}", response_model=EmployeeProfileResponse)
async def update_employee(
    employee_id: UUID,
    payload: EmployeeUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> EmployeeProfileResponse:
    """
    Updates an employee record. Prevents self-parenting/cycles in manager hierarchy
    and enforces role-based guards on CNIC and bank account modifications.
    """
    result = await EmployeeService.update_employee(
        session=db,
        tenant_id=current_user.tenant_id,
        employee_id=employee_id,
        payload=payload,
        actor=current_user,
    )
    await db.commit()
    return result


@router.get("/{employee_id}/documents", response_model=list[EmployeeDocumentResponse])
async def list_employee_documents(
    employee_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EmployeeDocumentResponse]:
    """Lists all documents in an employee's document vault."""
    return await EmployeeDocumentService.list_documents(
        session=db,
        tenant_id=current_user.tenant_id,
        employee_id=employee_id,
        actor=current_user,
    )


@router.post(
    "/{employee_id}/documents",
    response_model=EmployeeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_employee_document(
    employee_id: UUID,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    title: str | None = Form(None),
    expiry_date: str | None = Form(None),
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> EmployeeDocumentResponse:
    """
    Uploads a per-employee document (PDF, PNG, JPEG, DOCX up to 10MB) with optional expiry date.
    """
    parsed_expiry: date | None = None
    if expiry_date is not None and expiry_date.strip():
        try:
            parsed_expiry = date.fromisoformat(expiry_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid expiry_date '{expiry_date}'. Expected ISO format YYYY-MM-DD.",
            ) from exc

    file_bytes = await file.read()
    result = await EmployeeDocumentService.upload_document(
        session=db,
        tenant_id=current_user.tenant_id,
        employee_id=employee_id,
        document_type=document_type,
        title=title,
        expiry_date=parsed_expiry,
        file_bytes=file_bytes,
        filename=file.filename or "document.pdf",
        mime_type=file.content_type,
        actor=current_user,
    )
    await db.commit()
    return result


@router.get("/{employee_id}/documents/{document_id}/download")
async def download_employee_document(
    employee_id: UUID,
    document_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Downloads a per-employee document with hardened headers and checksum integrity."""
    content, file_name, mime_type = await EmployeeDocumentService.download_document(
        session=db,
        tenant_id=current_user.tenant_id,
        employee_id=employee_id,
        document_id=document_id,
        actor=current_user,
    )
    await db.commit()
    safe_header_name = (
        sanitize_filename(file_name)
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
        .replace("/", "_")
        .replace("\\", "_")
    )
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_header_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{employee_id}/documents/{document_id}")
async def delete_employee_document(
    employee_id: UUID,
    document_id: UUID,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Deletes a per-employee document. Restricted strictly to Owner and HR Head."""
    result = await EmployeeDocumentService.delete_document(
        session=db,
        tenant_id=current_user.tenant_id,
        employee_id=employee_id,
        document_id=document_id,
        actor=current_user,
    )
    await db.commit()
    return result
