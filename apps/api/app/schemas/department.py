"""
Pydantic Schemas for Stage 4 Department CRUD API.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DepartmentCreateRequest(BaseModel):
    """Request payload for creating a department."""

    name: str = Field(..., min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None)
    head_employee_id: UUID | None = Field(default=None)


class DepartmentUpdateRequest(BaseModel):
    """Request payload for updating a department."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None)
    head_employee_id: UUID | None = Field(default=None)


class DepartmentResponse(BaseModel):
    """Serialized department response with employee count."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    code: str | None = None
    description: str | None = None
    head_employee_id: UUID | None = None
    employee_count: int = 0
    created_at: datetime
    updated_at: datetime
