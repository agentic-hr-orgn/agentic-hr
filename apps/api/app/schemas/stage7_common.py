"""
Shared Stage 7 Pydantic types & literals for Policy Brain Chat and Helpdesk Tickets.
Prevents circular imports between `app.schemas.chat` and `app.schemas.ticket`.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

ChatLanguage = Literal["en", "ur", "roman_ur", "mixed"]

TicketCategory = Literal[
    "hr_policy",
    "leave_attendance",
    "payroll",
    "it_access",
    "facilities",
    "general",
]
TicketPriority = Literal["low", "medium", "high", "urgent"]


class CitationItem(BaseModel):
    """Strict citation reference verified against the active turn's retrieved chunks."""

    chunk_id: UUID
    document_id: UUID
    title: str = Field(..., min_length=1, max_length=255)
    version: int = Field(..., ge=1)
    section: str = Field(default="", max_length=500)
    page: int | str | None = None
    snippet: str = Field(..., min_length=1)

    @field_validator("page", mode="before")
    @classmethod
    def _normalize_page(cls, value: Any) -> int | str | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 1 else None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped.isdigit():
                parsed = int(stripped)
                return parsed if parsed >= 1 else None
            return stripped
        return str(value)
