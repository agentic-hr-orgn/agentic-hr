"""
Stage 6 Attendance Management REST API Endpoints (Prompt 4).

Endpoints (mounted under /api/v1/attendance):
- GET  /me/today
- POST /check-in
- GET  /daily
- PUT  /daily
- POST /import/validate
- POST /import/{batch_id}/commit
- POST /months/{year_month}/auto-fill
- GET  /months/{year_month}/review
- POST /months/{year_month}/lock
- POST /post-lock-edit
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
    require_owner_manager_or_hr_head,
)
from app.db.session import get_db
from app.schemas.attendance import (
    AttendanceAutoFillRequest,
    AttendanceAutoFillResponse,
    AttendanceCheckInRequest,
    AttendanceImportCommitResponse,
    AttendanceImportValidationResult,
    AttendanceMonthLockResponse,
    AttendanceMonthReviewResponse,
    AttendancePostLockEditRequest,
    AttendancePostLockEditResponse,
    AttendanceRecordResponse,
    AttendanceTodayResponse,
    DailyAttendanceGridResponse,
    DailyAttendanceUpsertRequest,
    DailyAttendanceUpsertResponse,
)
from app.services.attendance_service import MAX_ATTENDANCE_IMPORT_BYTES, AttendanceService
from app.services.storage_helper import read_upload_bytes_limited

router = APIRouter()


@router.get("/me/today", response_model=AttendanceTodayResponse)
async def get_my_today_attendance(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttendanceTodayResponse:
    """Returns today's Asia/Karachi date, `check_in_enabled`, `is_locked`, and caller's record."""
    return await AttendanceService.get_my_today(db=db, actor=current_user)


@router.post("/check-in", response_model=AttendanceRecordResponse)
async def self_check_in(
    payload: AttendanceCheckInRequest | None = None,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttendanceRecordResponse:
    """Employee self check-in / check-out (requires `work_week.check_in_enabled = true`)."""
    req = payload or AttendanceCheckInRequest()
    return await AttendanceService.check_in(db=db, actor=current_user, payload=req)


@router.get("/daily", response_model=DailyAttendanceGridResponse)
async def get_daily_attendance_grid(
    target_date: date = Query(...),
    department_id: UUID | None = Query(None),
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> DailyAttendanceGridResponse:
    """Lists daily attendance grid for manager's direct reports (or all employees for HR/Owner)."""
    return await AttendanceService.get_daily_grid(
        db=db,
        actor=current_user,
        target_date=target_date,
        department_id=department_id,
    )


@router.put("/daily", response_model=DailyAttendanceUpsertResponse)
async def upsert_daily_attendance_grid(
    payload: DailyAttendanceUpsertRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> DailyAttendanceUpsertResponse:
    """Batch upserts daily attendance statuses (`source='manager_grid'`). Refuses locked months."""
    return await AttendanceService.upsert_daily_grid(db=db, actor=current_user, payload=payload)


@router.post(
    "/import/validate",
    response_model=AttendanceImportValidationResult,
    dependencies=[
        Depends(RateLimiter(requests=60, window_seconds=60, scope="attendance_import.validate"))
    ],
)
async def validate_attendance_excel_import(
    file: UploadFile = File(...),
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> AttendanceImportValidationResult:
    """Phase 1 of Excel attendance import: magic-byte check, row validation, and Redis staging."""
    content = await read_upload_bytes_limited(
        file,
        MAX_ATTENDANCE_IMPORT_BYTES,
        error_code="IMPORT_FILE_TOO_LARGE",
    )
    return await AttendanceService.validate_attendance_xlsx(
        db=db,
        actor=current_user,
        file_bytes=content,
        filename=file.filename or "attendance.xlsx",
    )


@router.post(
    "/import/{batch_id}/commit",
    response_model=AttendanceImportCommitResponse,
    dependencies=[
        Depends(RateLimiter(requests=60, window_seconds=60, scope="attendance_import.commit"))
    ],
)
async def commit_attendance_excel_import(
    batch_id: str,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> AttendanceImportCommitResponse:
    """Phase 2 of Excel attendance import: atomically commits clean staged rows."""
    return await AttendanceService.commit_attendance_xlsx(
        db=db,
        actor=current_user,
        import_batch_id=batch_id,
    )


@router.post("/months/{year_month}/auto-fill", response_model=AttendanceAutoFillResponse)
async def auto_fill_month_attendance(
    year_month: str,
    payload: AttendanceAutoFillRequest | None = None,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> AttendanceAutoFillResponse:
    """Fills missing weekends, holidays, and approved leave dates for `year_month` (`YYYY-MM`)."""
    overwrite = payload.overwrite_existing if payload is not None else False
    return await AttendanceService.auto_fill_month(
        db=db,
        actor=current_user,
        year_month=year_month,
        overwrite_existing=overwrite,
    )


@router.get("/months/{year_month}/review", response_model=AttendanceMonthReviewResponse)
async def get_month_attendance_review(
    year_month: str,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> AttendanceMonthReviewResponse:
    """Returns per-employee monthly status tallies and exact missing attendance dates."""
    return await AttendanceService.get_month_review(
        db=db,
        actor=current_user,
        year_month=year_month,
    )


@router.post("/months/{year_month}/lock", response_model=AttendanceMonthLockResponse)
async def lock_month_attendance(
    year_month: str,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> AttendanceMonthLockResponse:
    """Locks an attendance month for payroll after verifying zero attendance gaps."""
    return await AttendanceService.lock_month(
        db=db,
        actor=current_user,
        year_month=year_month,
    )


@router.post(
    "/post-lock-edit",
    response_model=AttendancePostLockEditResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_post_lock_attendance_edit(
    payload: AttendancePostLockEditRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> AttendancePostLockEditResponse:
    """Creates a Stage 2 `attendance.post_lock_edit` approval for a locked attendance month."""
    return await AttendanceService.request_post_lock_edit(
        db=db,
        actor=current_user,
        payload=payload,
    )
