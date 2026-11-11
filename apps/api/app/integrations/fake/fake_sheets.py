from typing import Any, ClassVar

from app.integrations.base import SheetsService
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)


class FakeSheetsService(SheetsService):
    """
    Fake Google Sheets service maintaining an in-memory grid for testing.
    Enforces ADR 005 (Postgres is truth, Sheets is synced view) & ADR 006.
    """

    DEFAULT_SPREADSHEET_ID = "fake_sheet_employee_directory"
    DEFAULT_SHEET_NAME = "Employees"
    HEADERS: ClassVar[list[str]] = [
        "Employee ID",
        "Name",
        "Email",
        "Department",
        "Role",
        "Status",
    ]

    def __init__(self) -> None:
        self.simulate_401_on_next_call: bool = False
        self.simulate_429_on_next_call: bool = False
        self.simulate_503_on_next_call: bool = False
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
        self._spreadsheets: dict[str, dict[str, list[list[Any]]]] = {}
        self._initialize_default_sheet()

    def _initialize_default_sheet(self) -> None:
        initial_rows = [list(self.HEADERS)]
        for emp in self._synthetic_employees:
            initial_rows.append(
                [
                    emp["employee_id"],
                    emp["name"],
                    emp["email"],
                    emp["department"],
                    emp["role"],
                    emp["status"],
                ]
            )
        self._spreadsheets[self.DEFAULT_SPREADSHEET_ID] = {
            self.DEFAULT_SHEET_NAME: [list(r) for r in initial_rows]
        }

    def reset(self) -> None:
        """Resets the in-memory spreadsheets and flags to initial baseline."""
        self.simulate_401_on_next_call = False
        self.simulate_429_on_next_call = False
        self.simulate_503_on_next_call = False
        self._spreadsheets.clear()
        self._initialize_default_sheet()

    def _check_fault_injection(self) -> None:
        if self.simulate_401_on_next_call:
            self.simulate_401_on_next_call = False
            raise ConnectorAuthExpiredError("Simulated Google Sheets 401 Unauthorized")
        if self.simulate_429_on_next_call:
            self.simulate_429_on_next_call = False
            raise ConnectorRateLimitError(
                "Simulated Google Sheets 429 Too Many Requests", retry_after=1
            )
        if self.simulate_503_on_next_call:
            self.simulate_503_on_next_call = False
            raise ConnectorServiceUnavailableError(
                "Simulated Google Sheets 503 Service Unavailable"
            )

    async def get_employee_directory(
        self, spreadsheet_id: str | None = None
    ) -> list[dict[str, Any]]:
        return list(self._synthetic_employees)

    async def create_or_find_spreadsheet(self, title: str, access_token: str | None = None) -> str:
        self._check_fault_injection()

        # Find existing by title or return default
        for s_id in self._spreadsheets:
            if s_id == self.DEFAULT_SPREADSHEET_ID:
                return s_id

        new_id = f"fake_sheet_{title.lower().replace(' ', '_')}"
        if new_id not in self._spreadsheets:
            self._spreadsheets[new_id] = {self.DEFAULT_SHEET_NAME: [list(self.HEADERS)]}
        return new_id

    async def read_range(
        self,
        spreadsheet_id: str,
        range_name: str,
        access_token: str | None = None,
    ) -> list[list[Any]]:
        self._check_fault_injection()

        sheet = self._get_sheet(spreadsheet_id, range_name)
        # Parse range (e.g. 'Employees!A1:F10', 'Employees!A2:F', or 'A2:F')
        _, row_start, row_end = self._parse_range(range_name)

        if row_start is not None and row_start > 0:
            start_idx = row_start - 1
            end_idx = row_end if row_end is not None else len(sheet)
            return [list(r) for r in sheet[start_idx:end_idx]]

        return [list(r) for r in sheet]

    async def append_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[Any]],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        self._check_fault_injection()

        sheet = self._get_sheet(spreadsheet_id, range_name)
        for row in rows:
            sheet.append(list(row))

        return {
            "spreadsheetId": spreadsheet_id,
            "updates": {
                "updatedRange": range_name,
                "updatedRows": len(rows),
                "updatedColumns": max(len(r) for r in rows) if rows else 0,
                "updatedCells": sum(len(r) for r in rows),
            },
        }

    async def update_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[Any]],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        self._check_fault_injection()

        sheet = self._get_sheet(spreadsheet_id, range_name)
        _, row_start, _ = self._parse_range(range_name)

        start_idx = (row_start - 1) if (row_start is not None and row_start > 0) else 0
        for i, row in enumerate(rows):
            target_idx = start_idx + i
            if target_idx < len(sheet):
                sheet[target_idx] = list(row)
            else:
                sheet.append(list(row))

        return {
            "spreadsheetId": spreadsheet_id,
            "updatedRange": range_name,
            "updatedRows": len(rows),
            "updatedColumns": max(len(r) for r in rows) if rows else 0,
            "updatedCells": sum(len(r) for r in rows),
        }

    def inject_drift(
        self,
        spreadsheet_id: str,
        range_name: str,
        row_idx: int,
        col_idx: int,
        value: Any,
    ) -> None:
        """Simulates external unauthorized modification of a cell in Google Sheets."""
        sheet = self._get_sheet(spreadsheet_id, range_name)
        if 0 <= row_idx < len(sheet):
            row = sheet[row_idx]
            while len(row) <= col_idx:
                row.append("")
            row[col_idx] = value

    def _get_sheet(self, spreadsheet_id: str, range_name: str) -> list[list[Any]]:
        sheet_name, _, _ = self._parse_range(range_name)
        if spreadsheet_id not in self._spreadsheets:
            self._spreadsheets[spreadsheet_id] = {sheet_name: [list(self.HEADERS)]}
        if sheet_name not in self._spreadsheets[spreadsheet_id]:
            self._spreadsheets[spreadsheet_id][sheet_name] = [list(self.HEADERS)]
        return self._spreadsheets[spreadsheet_id][sheet_name]

    def _parse_range(self, range_name: str) -> tuple[str, int | None, int | None]:
        sheet_part = self.DEFAULT_SHEET_NAME
        cell_part = range_name

        if "!" in range_name:
            sheet_part, cell_part = range_name.split("!", 1)

        sheet_part = sheet_part.strip().strip("'")

        # Parse row numbers if present (e.g. A2:F10 -> 2, 10 or A2:F -> 2, None)
        row_start: int | None = None
        row_end: int | None = None

        if ":" in cell_part:
            start_cell, end_cell = cell_part.split(":", 1)
            start_digits = "".join(filter(str.isdigit, start_cell))
            end_digits = "".join(filter(str.isdigit, end_cell))
            if start_digits:
                row_start = int(start_digits)
            if end_digits:
                row_end = int(end_digits)
        else:
            digits = "".join(filter(str.isdigit, cell_part))
            if digits:
                row_start = int(digits)
                row_end = int(digits)

        return sheet_part, row_start, row_end
