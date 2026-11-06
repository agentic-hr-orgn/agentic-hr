from typing import Any

from app.integrations.base import SheetsService


class FakeSheetsService(SheetsService):
    """
    Fake Google Sheets service returning synthetic employee directory data.
    Enforces ADR 005 (Postgres is truth, Sheets is synced view) & ADR 006.
    """

    def __init__(self) -> None:
        self._synthetic_employees: list[dict[str, Any]] = [
            {
                "employee_id": "EMP-001",
                "name": "Ubaid Qureshi (Owner)",
                "email": "agentic.hr.test.ubaid.owner@gmail.com",
                "department": "Executive",
                "role": "Company Owner & Admin",
                "status": "Active",
            },
            {
                "employee_id": "EMP-002",
                "name": "Sarah Jenkins (Recruiter)",
                "email": "agentic.hr.test.ubaid.recruiter@gmail.com",
                "department": "HR",
                "role": "Talent Acquisition Lead",
                "status": "Active",
            },
            {
                "employee_id": "EMP-003",
                "name": "Tariq Mahmood (Hiring Manager)",
                "email": "agentic.hr.test.ubaid.hm@gmail.com",
                "department": "Engineering",
                "role": "VP of Engineering",
                "status": "Active",
            },
            {
                "employee_id": "EMP-004",
                "name": "Zainab Ali (Employee)",
                "email": "agentic.hr.test.ubaid.employee@gmail.com",
                "department": "Engineering",
                "role": "Senior Full-Stack Engineer",
                "status": "Active",
            },
            {
                "employee_id": "EMP-005",
                "name": "Bilal Ahmed",
                "email": "bilal.ahmed@acmetech.internal",
                "department": "Sales",
                "role": "Account Executive",
                "status": "Active",
            },
            {
                "employee_id": "EMP-006",
                "name": "Fatima Noor",
                "email": "fatima.noor@acmetech.internal",
                "department": "Operations",
                "role": "Operations Manager",
                "status": "Active",
            },
        ]

    async def get_employee_directory(
        self, spreadsheet_id: str | None = None
    ) -> list[dict[str, Any]]:
        return list(self._synthetic_employees)
