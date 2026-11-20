"""
Authoritative Timezone Normalization Utilities for Agentic HR Core.
Enforces Asia/Karachi (PKT, UTC+05:00) across all Calendar, Sheets, and MCP timestamp outputs.
Uses zoneinfo.ZoneInfo with a fixed UTC+05:00 fallback for tzdata-less Windows environments.
"""

import zoneinfo
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Annotated

from pydantic import AfterValidator

KARACHI_TZ_NAME = "Asia/Karachi"
DEFAULT_TIMEZONE = KARACHI_TZ_NAME


def get_karachi_tz(tz_name: str = KARACHI_TZ_NAME) -> tzinfo:
    """Returns ZoneInfo for tz_name if available, otherwise fixed UTC+05:00 offset."""
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return timezone(timedelta(hours=5), name=tz_name)


def to_karachi(dt: datetime, source_tz: str = KARACHI_TZ_NAME) -> datetime:
    """
    Normalizes any naive or timezone-aware datetime to Asia/Karachi (PKT, UTC+05:00).
    - If dt is naive, it is first interpreted in source_tz (defaults to Asia/Karachi),
      then converted to Asia/Karachi.
    - If dt is timezone-aware, it is converted directly to Asia/Karachi.
    """
    karachi_tz = get_karachi_tz(KARACHI_TZ_NAME)
    if dt.tzinfo is None:
        assumed_tz = get_karachi_tz(source_tz) if source_tz != KARACHI_TZ_NAME else karachi_tz
        dt = dt.replace(tzinfo=assumed_tz)
    return dt.astimezone(karachi_tz)


# Backward-compatible alias used by GoogleCalendarService / FakeCalendarService
def normalize_to_karachi_tz(dt: datetime, default_tz: str = KARACHI_TZ_NAME) -> datetime:
    """Normalizes naive or foreign datetime objects to Asia/Karachi (PKT, UTC+05:00)."""
    return to_karachi(dt, source_tz=default_tz)


def iso_pkt(dt: datetime | None) -> str | None:
    """Serializes a datetime to an ISO-8601 string normalized to Asia/Karachi (+05:00)."""
    if dt is None:
        return None
    return to_karachi(dt).isoformat()


KarachiDatetime = Annotated[datetime, AfterValidator(to_karachi)]
