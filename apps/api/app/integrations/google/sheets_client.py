"""
Real Google Sheets Service Client for Agentic HR Core.
Complies with ADR 005 (Postgres is truth, Sheets is synced view) and ADR 006 (drive.file scope).
Communicates asynchronously via HTTPX with Google Sheets v4 and Drive v3 APIs.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.integrations.base import SheetsService
from app.services.connector_service import ConnectorAuthExpiredError

logger = logging.getLogger("agentic_hr.google_sheets")


class GoogleSheetsService(SheetsService):
    """
    Real Google Sheets client operating under the least-privilege drive.file scope.
    Handles token authentication, cell formatting, exponential backoff, and 401 expiration.
    """

    DRIVE_API_URL = "https://www.googleapis.com/drive/v3/files"
    SHEETS_API_URL = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(
        self,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    async def get_employee_directory(
        self, spreadsheet_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Reads rows from the synced view and formats as a dictionary list."""
        if not spreadsheet_id:
            raise ValueError("spreadsheet_id is required for real Google Sheets operations.")
        raw_rows = await self.read_range(spreadsheet_id, "Employees!A2:F")
        records: list[dict[str, Any]] = []
        for r in raw_rows:
            if not r:
                continue
            records.append(
                {
                    "employee_id": r[0] if len(r) > 0 else "",
                    "name": r[1] if len(r) > 1 else "",
                    "email": r[2] if len(r) > 2 else "",
                    "department": r[3] if len(r) > 3 else "",
                    "role": r[4] if len(r) > 4 else "",
                    "status": r[5] if len(r) > 5 else "Active",
                }
            )
        return records

    async def create_or_find_spreadsheet(self, title: str, access_token: str | None = None) -> str:
        """
        Locates an existing spreadsheet matching title under drive.file,
        or creates a new spreadsheet and initializes headers.
        """
        if not access_token:
            raise ConnectorAuthExpiredError("Missing Google OAuth access token.")

        headers = {"Authorization": f"Bearer {access_token}"}

        # 1. Search in Drive
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            query = (
                f"name = '{title}' and "
                "mimeType = 'application/vnd.google-apps.spreadsheet' and "
                "trashed = false"
            )
            resp = await client.get(
                self.DRIVE_API_URL,
                headers=headers,
                params={"q": query, "fields": "files(id, name)"},
            )
            self._check_for_auth_failure(resp)

            if resp.status_code == 200:
                files = resp.json().get("files", [])
                if files:
                    return files[0]["id"]

            # 2. Not found -> Create new spreadsheet
            create_payload = {
                "properties": {"title": title},
                "sheets": [
                    {
                        "properties": {
                            "title": "Employees",
                            "gridProperties": {"frozenRowCount": 1},
                        }
                    }
                ],
            }
            create_resp = await client.post(
                self.SHEETS_API_URL, headers=headers, json=create_payload
            )
            self._check_for_auth_failure(create_resp)
            if create_resp.status_code not in (200, 201):
                raise RuntimeError(
                    f"Failed to create Google Sheet '{title}': {create_resp.status_code} {create_resp.text}"
                )

            created_sheet = create_resp.json()
            spreadsheet_id = created_sheet["spreadsheetId"]

            # 3. Add default headers
            header_rows = [["Employee ID", "Name", "Email", "Department", "Role", "Status"]]
            await self.update_rows(spreadsheet_id, "Employees!A1:F1", header_rows, access_token)
            return spreadsheet_id

    async def read_range(
        self,
        spreadsheet_id: str,
        range_name: str,
        access_token: str | None = None,
    ) -> list[list[Any]]:
        """Reads rows from the specified range with automatic retry on 429/503."""
        if not access_token:
            raise ConnectorAuthExpiredError("Missing Google OAuth access token.")

        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{self.SHEETS_API_URL}/{spreadsheet_id}/values/{range_name}"

        return await self._execute_with_retry(
            method="GET",
            url=url,
            headers=headers,
            parse_key="values",
        )

    async def append_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[Any]],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Appends rows to the specified spreadsheet."""
        if not access_token:
            raise ConnectorAuthExpiredError("Missing Google OAuth access token.")

        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{self.SHEETS_API_URL}/{spreadsheet_id}/values/{range_name}:append"
        params = {"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}
        payload = {"values": rows}

        return await self._execute_with_retry(
            method="POST",
            url=url,
            headers=headers,
            params=params,
            json=payload,
        )

    async def update_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[Any]],
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Overwrites specific cells/ranges in the spreadsheet."""
        if not access_token:
            raise ConnectorAuthExpiredError("Missing Google OAuth access token.")

        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"{self.SHEETS_API_URL}/{spreadsheet_id}/values/{range_name}"
        params = {"valueInputOption": "USER_ENTERED"}
        payload = {"values": rows}

        return await self._execute_with_retry(
            method="PUT",
            url=url,
            headers=headers,
            params=params,
            json=payload,
        )

    async def _execute_with_retry(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        parse_key: str | None = None,
        max_attempts: int = 3,
    ) -> Any:
        """Executes HTTP request with exponential backoff for transient 429/503 errors."""
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    if method == "GET":
                        resp = await client.get(url, headers=headers, params=params)
                    elif method == "POST":
                        resp = await client.post(url, headers=headers, params=params, json=json)
                    elif method == "PUT":
                        resp = await client.put(url, headers=headers, params=params, json=json)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")

                    self._check_for_auth_failure(resp)

                    if resp.status_code in (429, 503) and attempt < max_attempts:
                        delay = (2 ** (attempt - 1)) * 0.5
                        logger.warning(
                            f"Sheets API returned {resp.status_code}, retrying in {delay}s (attempt {attempt}/{max_attempts})"
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status_code not in (200, 201):
                        raise RuntimeError(
                            f"Google Sheets API failed ({resp.status_code}): {resp.text}"
                        )

                    data = resp.json()
                    return data.get(parse_key, []) if parse_key else data

                except httpx.RequestError as exc:
                    if attempt < max_attempts:
                        delay = (2 ** (attempt - 1)) * 0.5
                        await asyncio.sleep(delay)
                        continue
                    raise RuntimeError(
                        f"Network error communicating with Google Sheets: {exc}"
                    ) from exc

        raise RuntimeError("Google Sheets request failed after all retry attempts.")

    @staticmethod
    def _check_for_auth_failure(response: httpx.Response) -> None:
        """Inspects response status and raises ConnectorAuthExpiredError on 401."""
        if response.status_code == 401 or "invalid_grant" in response.text:
            raise ConnectorAuthExpiredError(
                f"Google OAuth token expired or revoked (401): {response.text}"
            )
