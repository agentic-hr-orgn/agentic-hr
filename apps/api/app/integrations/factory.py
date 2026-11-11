from app.core.config import settings
from app.integrations.base import (
    CalendarService,
    DriveService,
    EmailService,
    SheetsService,
)
from app.integrations.fake.fake_calendar import FakeCalendarService
from app.integrations.fake.fake_drive import FakeDriveService
from app.integrations.fake.fake_gmail import FakeGmailService
from app.integrations.fake.fake_sheets import FakeSheetsService
from app.integrations.google.calendar_client import GoogleCalendarService
from app.integrations.google.drive_client import GoogleDriveService
from app.integrations.google.gmail_client import GoogleGmailService
from app.integrations.google.sheets_client import GoogleSheetsService

# Global singletons for fake services in Stage 1
_fake_email_service = FakeGmailService()
_fake_calendar_service = FakeCalendarService()
_fake_drive_service = FakeDriveService()
_fake_sheets_service = FakeSheetsService()


def get_email_service() -> EmailService:
    if settings.USE_FAKE_INTEGRATIONS:
        return _fake_email_service
    return GoogleGmailService()


def get_calendar_service() -> CalendarService:
    if settings.USE_FAKE_INTEGRATIONS:
        return _fake_calendar_service
    return GoogleCalendarService()


def get_drive_service() -> DriveService:
    if settings.USE_FAKE_INTEGRATIONS:
        return _fake_drive_service
    return GoogleDriveService()


def get_sheets_service() -> SheetsService:
    if settings.USE_FAKE_INTEGRATIONS:
        return _fake_sheets_service
    return GoogleSheetsService()
