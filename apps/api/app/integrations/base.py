"""
Abstract Base Classes & Typed Schemas for Agentic HR Connector Layer.
Complies with:
- ADR 005 (Postgres is Authoritative, Sheets is Synced View)
- ADR 006 & docs/oauth-scopes.md (Least-privilege scopes: gmail.send, drive.file, calendar.events)
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# =============================================================================
# Gmail / Email Schemas
# =============================================================================
class EmailMessage(BaseModel):
    to_email: str
    subject: str
    body_text: str
    body_html: str | None = None
    sender_email: str | None = None
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class EmailSendResult(BaseModel):
    message_id: str
    status: str = "sent"
    fake: bool = False
    provider_message_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class GmailDeliveryError(Exception):
    """Raised when Gmail API or SMTP returns a synchronous delivery rejection (e.g. 400 Bad Request, 550 SMTP rejection)."""


# =============================================================================
# Google Drive Schemas (drive.file scope)
# =============================================================================
class DriveFile(BaseModel):
    file_id: str
    name: str
    mime_type: str
    size_bytes: int
    web_view_link: str | None = None
    folder_id: str | None = None
    created_time: datetime | None = None
    modified_time: datetime | None = None


class DriveFolder(BaseModel):
    folder_id: str
    name: str
    web_view_link: str | None = None
    parent_folder_id: str | None = None


# =============================================================================
# Google Calendar Schemas (calendar.events scope)
# =============================================================================
class CalendarEvent(BaseModel):
    title: str
    start_time: datetime
    end_time: datetime
    attendees: list[str] = Field(default_factory=list)
    description: str | None = None
    location: str | None = "Google Meet"
    calendar_id: str = "primary"
    event_id: str | None = None


class CalendarEventUpdate(BaseModel):
    title: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    attendees: list[str] | None = None
    description: str | None = None
    location: str | None = None


class TimeSlot(BaseModel):
    start_time: datetime
    end_time: datetime


class FreeBusyResponse(BaseModel):
    calendar_id: str
    busy_slots: list[TimeSlot] = Field(default_factory=list)


class CalendarEventResult(BaseModel):
    event_id: str
    status: str = "confirmed"
    hangout_link: str | None = None
    fake: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Abstract Connector Service Interfaces
# =============================================================================
class EmailService(ABC):
    """Abstract Email service restricted strictly to SEND-ONLY per ADR 006."""

    @abstractmethod
    async def send_email(
        self,
        message: EmailMessage,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Send an email. Reading or modifying inbox is strictly disallowed."""


class SheetsService(ABC):
    """Abstract Sheets service treating Sheets as a Synced View per ADR 005."""

    @abstractmethod
    async def get_employee_directory(
        self, spreadsheet_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch employee directory from synced view."""

    @abstractmethod
    async def create_or_find_spreadsheet(self, title: str, access_token: str | None = None) -> str:
        """Finds or creates a spreadsheet under drive.file scope; returns spreadsheet_id."""

    @abstractmethod
    async def read_range(
        self,
        spreadsheet_id: str,
        range_name: str,
        access_token: str | None = None,
    ) -> list[list[Any]]:
        """Reads cell grid values from a sheet range (e.g. 'Employees!A1:F100')."""

    @abstractmethod
    async def append_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[Any]],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Appends rows to the spreadsheet."""

    @abstractmethod
    async def update_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[Any]],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Updates specific row/cell ranges in the spreadsheet."""


class DriveService(ABC):
    """Abstract Drive service restricted to drive.file per ADR 006."""

    @abstractmethod
    async def ensure_tenant_folder(
        self,
        folder_name: str,
        parent_folder_id: str | None = None,
        access_token: str | None = None,
    ) -> str:
        """Ensures a dedicated tenant folder exists under drive.file; returns folder_id."""

    @abstractmethod
    async def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str,
        folder_id: str | None = None,
        access_token: str | None = None,
    ) -> DriveFile:
        """Upload a file (e.g., CV) created or opened by the app."""

    @abstractmethod
    async def download_file(
        self,
        file_id: str,
        access_token: str | None = None,
    ) -> bytes:
        """Downloads the binary content of a file owned by the app."""

    @abstractmethod
    async def list_files(
        self,
        folder_id: str | None = None,
        page_size: int = 50,
        access_token: str | None = None,
    ) -> list[DriveFile]:
        """Lists files in the tenant folder created/opened by the app."""


class CalendarService(ABC):
    """Abstract Calendar service restricted to calendar.events per ADR 006."""

    @abstractmethod
    async def get_free_busy(
        self,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        access_token: str | None = None,
    ) -> dict[str, list[TimeSlot]]:
        """Queries free/busy windows for given calendar IDs within the time window."""

    @abstractmethod
    async def check_conflicts(
        self,
        calendar_ids: list[str],
        start_time: datetime,
        end_time: datetime,
        access_token: str | None = None,
    ) -> bool:
        """Returns True if any busy slot conflicts with the requested window."""

    @abstractmethod
    async def create_event(
        self,
        event: CalendarEvent,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Creates an interview or meeting event with Google Meet link."""

    @abstractmethod
    async def update_event(
        self,
        event_id: str,
        event: CalendarEventUpdate,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Updates/reschedules an existing calendar event."""

    @abstractmethod
    async def cancel_event(
        self,
        event_id: str,
        access_token: str | None = None,
    ) -> bool:
        """Cancels/deletes an existing calendar event."""
