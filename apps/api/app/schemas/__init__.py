"""
Shared Pydantic domain models and DTO schemas.
"""

from app.schemas.calendar import (
    CalendarEventCreateRequest,
    CalendarEventResponse,
    CalendarEventUpdateRequest,
    FreeBusyQueryRequest,
    FreeBusyQueryResponse,
    TimeSlotSchema,
)
from app.schemas.drive import (
    DriveFileListResponse,
    DriveFileResponse,
    DriveFolderEnsureRequest,
    DriveFolderResponse,
    DriveUploadRequest,
)
from app.schemas.errors import ErrorDetail, ErrorResponse
from app.schemas.health import DatabaseStatus, HealthResponse, ModelRoutingInfo

__all__ = [
    "CalendarEventCreateRequest",
    "CalendarEventResponse",
    "CalendarEventUpdateRequest",
    "DatabaseStatus",
    "DriveFileListResponse",
    "DriveFileResponse",
    "DriveFolderEnsureRequest",
    "DriveFolderResponse",
    "DriveUploadRequest",
    "ErrorDetail",
    "ErrorResponse",
    "FreeBusyQueryRequest",
    "FreeBusyQueryResponse",
    "HealthResponse",
    "ModelRoutingInfo",
    "TimeSlotSchema",
]
