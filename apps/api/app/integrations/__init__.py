"""Integration services abstraction layer (ADR 006)."""

from app.integrations.factory import (
    get_calendar_service,
    get_drive_service,
    get_email_service,
    get_sheets_service,
)

__all__ = [
    "get_calendar_service",
    "get_drive_service",
    "get_email_service",
    "get_sheets_service",
]
