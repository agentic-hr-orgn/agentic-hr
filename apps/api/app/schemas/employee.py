"""
Pydantic Schemas for Stage 4 Employee Directory, Role-Based Profiles,
Org Chart, and Per-Employee Document Vault APIs.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.timezones import KarachiDatetime

EmploymentType = Literal["full_time", "part_time", "contract", "intern"]
EmployeeStatus = Literal["active", "invited", "on_leave", "terminated", "resigned"]
DocumentType = Literal["cnic", "contract", "certificate", "other"]


class EmployeeListItem(BaseModel):
    """
    Non-sensitive employee directory item projected strictly from `public.pub_employees`.
    Never contains CNIC or bank account fields.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID | None = None
    employee_code: str | None = None
    full_name: str
    email: str
    phone: str | None = None
    department_id: UUID | None = None
    department_name: str | None = None
    manager_id: UUID | None = None
    manager_name: str | None = None
    job_title: str
    employment_type: str
    join_date: date
    leaving_date: date | None = None
    status: str
    created_at: KarachiDatetime
    updated_at: KarachiDatetime


class EmployeeListResponse(BaseModel):
    """Paginated employee directory response."""

    items: list[EmployeeListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class EmployeeProfileResponse(EmployeeListItem):
    """
    Detailed employee profile response with strict role-based PII fields.
    `cnic` and `bank_account` are populated ONLY when `can_view_sensitive` is True.
    """

    manager_email: str | None = None
    can_view_sensitive: bool = False
    can_edit_sensitive: bool = False
    cnic: str | None = None
    bank_account: str | None = None
    cnic_masked: str | None = None
    bank_account_masked: str | None = None


class EmployeeCreateRequest(BaseModel):
    """Request payload for creating an employee record."""

    full_name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=3, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    employee_code: str | None = Field(default=None, max_length=64)
    user_id: UUID | None = Field(default=None)
    department_id: UUID | None = Field(default=None)
    manager_id: UUID | None = Field(default=None)
    job_title: str = Field(..., min_length=1, max_length=150)
    employment_type: EmploymentType = Field(default="full_time")
    join_date: date
    leaving_date: date | None = Field(default=None)
    status: EmployeeStatus = Field(default="active")
    cnic: str | None = Field(default=None, max_length=64)
    bank_account: str | None = Field(default=None, max_length=128)


class EmployeeUpdateRequest(BaseModel):
    """Request payload for partially updating an employee record."""

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    employee_code: str | None = Field(default=None, max_length=64)
    user_id: UUID | None = Field(default=None)
    department_id: UUID | None = Field(default=None)
    manager_id: UUID | None = Field(default=None)
    job_title: str | None = Field(default=None, min_length=1, max_length=150)
    employment_type: EmploymentType | None = Field(default=None)
    join_date: date | None = Field(default=None)
    leaving_date: date | None = Field(default=None)
    status: EmployeeStatus | None = Field(default=None)
    cnic: str | None = Field(default=None, max_length=64)
    bank_account: str | None = Field(default=None, max_length=128)


class OrgChartNode(BaseModel):
    """Recursive node in the tenant organizational chart."""

    id: UUID
    employee_code: str | None = None
    full_name: str
    job_title: str
    department_name: str | None = None
    status: str
    email: str
    manager_id: UUID | None = None
    direct_reports_count: int = 0
    children: list["OrgChartNode"] = Field(default_factory=list)


OrgChartNode.model_rebuild()


class OrgChartResponse(BaseModel):
    """Cycle-safe organizational chart hierarchy built from `public.pub_employees`."""

    total_employees: int
    roots: list[OrgChartNode]
    unassigned: list[OrgChartNode]


class EmployeeDocumentResponse(BaseModel):
    """Serialized per-employee document vault record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    employee_id: UUID
    document_type: str
    title: str
    file_name: str
    mime_type: str
    size_bytes: int
    storage_provider: str
    storage_key: str
    web_view_link: str | None = None
    expiry_date: date | None = None
    uploaded_by: UUID | None = None
    created_at: KarachiDatetime
    updated_at: KarachiDatetime
