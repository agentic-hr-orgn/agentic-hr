"""
Excel Bulk Employee Importer, Strict Multi-Pass Validator, Error Report Generator,
and Two-Phase Commit Service for Agentic HR Core (Stage 4 Prompt 3).

Security & Governance Invariants:
- Strict tenant isolation: staging batches and commit locks are keyed by `(tenant_id, import_batch_id)`.
- Zero plaintext PII in Redis staging: `cnic` and `bank_account` are encrypted via `encrypt_field`
  during staging and re-encrypted with canonical 4-field employee AAD
  `(tenant_id, "employee", emp_id, field_name)` using pre-generated `emp_id = uuid.uuid4()` on commit.
- Never echoes raw CNIC or bank account numbers in preview rows or downloadable `.xlsx` error reports.
- Prevents Excel formula injection (`=`, `+`, `-`, `@`, `\\t`, `\\r`) when rendering user strings
  into the downloadable `.xlsx` error report.
- Guards `commit_import_batch` with a tenant-scoped Redis NX lock (60s TTL, with in-memory fallback)
  returning HTTP 409 `IMPORT_BATCH_COMMIT_IN_PROGRESS` on concurrent double-clicks.
"""

import io
import json
import logging
import re
import time
import uuid
from datetime import date, datetime
from typing import Any
from uuid import UUID

import openpyxl
from fastapi import HTTPException, status
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionContext, decrypt_field, encrypt_field
from app.core.redis import get_redis_client
from app.models.department import Department
from app.models.employee import Employee
from app.schemas.employee_import import (
    ImportCommitResponse,
    ImportPreviewRow,
    ImportRowError,
    ImportValidationResult,
)
from app.services.audit_service import write_audit
from app.services.employee_service import EmployeeService
from app.services.leave_config_service import initialize_employee_leave_balances
from app.services.sheets_sync_service import SheetsSyncService
from app.services.storage_helper import XLSX_MIME, validate_file_magic_bytes

logger = logging.getLogger("agentic_hr.services.employee_import")

IMPORT_COLUMNS: tuple[str, ...] = (
    "full_name",
    "email",
    "phone",
    "department",
    "manager_email",
    "job_title",
    "employment_type",
    "join_date",
    "cnic",
    "bank_account",
)

REQUIRED_COLUMNS: tuple[str, ...] = (
    "full_name",
    "email",
    "department",
    "job_title",
    "employment_type",
    "join_date",
)

VALID_EMPLOYMENT_TYPES: tuple[str, ...] = (
    "full_time",
    "part_time",
    "contract",
    "intern",
)

MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMPORT_ROWS = 500
STAGING_TTL_SECONDS = 1800  # 30 minutes
COMMIT_LOCK_TTL_SECONDS = 60

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
CNIC_REGEX = re.compile(r"^(\d{5}-\d{7}-\d{1}|\d{13})$")
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

# Fallback in-memory TTL store & commit locks when Redis is unreachable in local/offline test runs
_MEMORY_STAGING_STORE: dict[str, tuple[float, str]] = {}
_MEMORY_COMMIT_LOCKS: dict[str, float] = {}


def _staging_redis_key(tenant_id: UUID, batch_id: str) -> str:
    return f"stage4:emp_import:{tenant_id}:{batch_id}"


def _commit_lock_redis_key(tenant_id: UUID, batch_id: str) -> str:
    return f"stage4:emp_import_lock:{tenant_id}:{batch_id}"


def _sanitize_excel_cell(val: str | None) -> str:
    """
    Prevents Excel / CSV formula injection by prefixing strings that start with
    formula trigger characters (`=`, `+`, `-`, `@`, tab, CR) with a single quote `'`.
    """
    if not val:
        return ""
    s = str(val)
    stripped = s.lstrip(" ")
    if stripped.startswith(_FORMULA_TRIGGERS):
        return f"'{s}"
    return s


def _staging_aad_context(
    tenant_id: UUID,
    batch_id: str,
    row_number: int,
    field_name: str,
) -> EncryptionContext:
    return EncryptionContext(
        tenant_id=tenant_id,
        entity_type="employee_import_staging",
        record_id=batch_id,
        field_name=f"row_{row_number}_{field_name}",
    )


def _normalize_date_cell(raw_val: Any) -> tuple[str, str | None]:
    """
    Normalizes an Excel date cell (datetime, date, or string) into a display string
    and a validated ISO `YYYY-MM-DD` string (or None if invalid).
    """
    if raw_val is None:
        return "", None
    if isinstance(raw_val, datetime):
        iso = raw_val.date().isoformat()
        return iso, iso
    if isinstance(raw_val, date):
        iso = raw_val.isoformat()
        return iso, iso
    s = str(raw_val).strip()
    if not s:
        return "", None
    # Strip trailing time portion if Excel exported 'YYYY-MM-DD 00:00:00'
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        candidate = s[:10]
        try:
            parsed = date.fromisoformat(candidate)
            if candidate == s or s[10:].strip() in ("00:00:00", "T00:00:00"):
                return candidate, parsed.isoformat()
        except ValueError:
            return s, None
    return s, None


async def _save_staging_payload(
    tenant_id: UUID,
    batch_id: str,
    payload: dict[str, Any],
) -> None:
    key = _staging_redis_key(tenant_id, batch_id)
    serialized = json.dumps(payload)
    _MEMORY_STAGING_STORE[key] = (time.time() + STAGING_TTL_SECONDS, serialized)
    try:
        redis = await get_redis_client()
        await redis.set(key, serialized, ex=STAGING_TTL_SECONDS)
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis staging write fell back to memory for {key}: {exc}")


async def _load_staging_payload(
    tenant_id: UUID,
    batch_id: str,
) -> dict[str, Any] | None:
    key = _staging_redis_key(tenant_id, batch_id)
    raw_json: str | None = None
    try:
        redis = await get_redis_client()
        raw_json = await redis.get(key)
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis staging read fell back to memory for {key}: {exc}")

    if not raw_json:
        entry = _MEMORY_STAGING_STORE.get(key)
        if entry:
            expires_at, val = entry
            if time.time() <= expires_at:
                raw_json = val
            else:
                _MEMORY_STAGING_STORE.pop(key, None)

    if not raw_json:
        return None
    data = json.loads(raw_json)
    if data.get("tenant_id") != str(tenant_id):
        return None
    return data


async def _delete_staging_payload(tenant_id: UUID, batch_id: str) -> None:
    key = _staging_redis_key(tenant_id, batch_id)
    _MEMORY_STAGING_STORE.pop(key, None)
    try:
        redis = await get_redis_client()
        await redis.delete(key)
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis staging delete fell back to memory for {key}: {exc}")


async def _acquire_commit_lock(tenant_id: UUID, batch_id: str) -> bool:
    key = _commit_lock_redis_key(tenant_id, batch_id)
    now = time.time()
    mem_exp = _MEMORY_COMMIT_LOCKS.get(key)
    if mem_exp is not None and now < mem_exp:
        return False
    _MEMORY_COMMIT_LOCKS[key] = now + COMMIT_LOCK_TTL_SECONDS

    try:
        redis = await get_redis_client()
        acquired = await redis.set(key, "1", nx=True, ex=COMMIT_LOCK_TTL_SECONDS)
        if not acquired:
            return False
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis commit lock fell back to memory for {key}: {exc}")
    return True


async def _release_commit_lock(tenant_id: UUID, batch_id: str) -> None:
    key = _commit_lock_redis_key(tenant_id, batch_id)
    _MEMORY_COMMIT_LOCKS.pop(key, None)
    try:
        redis = await get_redis_client()
        await redis.delete(key)
    except (RedisError, OSError) as exc:
        logger.debug(f"Redis commit unlock fell back to memory for {key}: {exc}")


class EmployeeImportService:
    """Orchestrates Excel template generation, strict multi-pass validation, error reports, and commit."""

    @staticmethod
    async def generate_template_xlsx(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
    ) -> bytes:
        """
        Builds a styled `.xlsx` workbook with:
        - Sheet 1 (`Employees`): Headers for all 10 bulk import columns.
        - Sheet 2 (`Instructions`): Valid `employment_type` values, `YYYY-MM-DD` date format,
          CNIC format, and the tenant's existing department names.
        """
        t_id = UUID(str(tenant_id))
        dept_rows = (
            (
                await session.execute(
                    select(Department.name)
                    .where(Department.tenant_id == t_id)
                    .order_by(Department.name.asc())
                )
            )
            .scalars()
            .all()
        )
        dept_names = list(dept_rows)

        wb = openpyxl.Workbook()
        ws_emp = wb.active
        ws_emp.title = "Employees"

        header_fill = PatternFill(start_color="102C54", end_color="102C54", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, name="Calibri", size=11)
        header_align = Alignment(horizontal="center", vertical="center")

        for col_idx, col_name in enumerate(IMPORT_COLUMNS, start=1):
            cell = ws_emp.cell(row=1, column=col_idx, value=col_name)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_align
            col_letter = get_column_letter(col_idx)
            ws_emp.column_dimensions[col_letter].width = max(len(col_name) + 8, 18)

        ws_emp.freeze_panes = "A2"

        ws_inst = wb.create_sheet(title="Instructions")
        title_font = Font(color="102C54", bold=True, name="Calibri", size=13)
        sub_font = Font(color="2A5BDB", bold=True, name="Calibri", size=11)

        ws_inst["A1"] = "Agentic HR Core — Bulk Employee Import Instructions"
        ws_inst["A1"].font = title_font

        instructions_rows: list[tuple[str, str]] = [
            ("Required Columns", ", ".join(REQUIRED_COLUMNS)),
            ("Optional Columns", "phone, manager_email, cnic, bank_account"),
            ("Valid employment_type Values", ", ".join(VALID_EMPLOYMENT_TYPES)),
            ("Date Format (join_date)", "YYYY-MM-DD (e.g. 2026-01-15)"),
            ("CNIC Format (cnic)", "13-digit Pakistani CNIC: XXXXX-XXXXXXX-X or 13 digits"),
            ("Upload Limits", f"Max {MAX_IMPORT_ROWS} rows and max 5 MB per .xlsx file"),
            (
                "Existing Tenant Departments",
                ", ".join(dept_names) if dept_names else "(No departments created yet)",
            ),
        ]

        for idx, (label, desc) in enumerate(instructions_rows, start=3):
            c_label = ws_inst.cell(row=idx, column=1, value=label)
            c_label.font = sub_font
            ws_inst.cell(row=idx, column=2, value=desc)

        if dept_names:
            ws_inst.cell(
                row=11, column=1, value="Valid Department Names (Exact Match):"
            ).font = sub_font
            for d_idx, d_name in enumerate(dept_names, start=12):
                ws_inst.cell(row=d_idx, column=1, value=d_name)

        ws_inst.column_dimensions["A"].width = 34
        ws_inst.column_dimensions["B"].width = 72

        buf = io.BytesIO()
        try:
            wb.save(buf)
        finally:
            wb.close()
        return buf.getvalue()

    @classmethod
    async def validate_upload_xlsx(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        file_bytes: bytes,
        filename: str,
    ) -> ImportValidationResult:
        """
        Parses `.xlsx` with `read_only=True, data_only=True`, skips completely blank rows,
        enforces 5MB / 500-row limits, validates all 6 rule categories across every row,
        encrypts staging PII, and stores staging state in Redis (30-min TTL).
        """
        t_id = UUID(str(tenant_id))
        clean_filename = (filename or "employees.xlsx").strip()
        if not clean_filename.lower().endswith(".xlsx"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="INVALID_XLSX_FILE: Only .xlsx Excel workbooks are supported.",
            )
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="IMPORT_FILE_EMPTY: Uploaded file cannot be empty.",
            )
        if len(file_bytes) > MAX_IMPORT_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"IMPORT_FILE_TOO_LARGE: File size ({len(file_bytes)} bytes) exceeds "
                    f"maximum allowed limit of {MAX_IMPORT_FILE_BYTES} bytes (5MB)."
                ),
            )

        validate_file_magic_bytes(
            file_bytes,
            XLSX_MIME,
            error_code="INVALID_XLSX_FILE",
        )

        try:
            wb = openpyxl.load_workbook(
                io.BytesIO(file_bytes),
                read_only=True,
                data_only=True,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"INVALID_XLSX_FILE: Could not parse .xlsx workbook: {exc}",
            ) from exc

        parsed_rows: list[tuple[int, dict[str, Any]]] = []
        try:
            ws = wb["Employees"] if "Employees" in wb.sheetnames else wb.active
            row_iter = ws.iter_rows(values_only=True)
            header_tuple = next(row_iter, None)
            if not header_tuple:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="IMPORT_FILE_EMPTY: Workbook contains no header row.",
                )

            header_map: dict[str, int] = {}
            for idx, col_val in enumerate(header_tuple):
                if col_val is not None:
                    norm = str(col_val).strip().lower()
                    if norm and norm not in header_map:
                        header_map[norm] = idx

            missing_headers = [c for c in REQUIRED_COLUMNS if c not in header_map]
            if missing_headers:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"MISSING_REQUIRED_COLUMNS: Workbook is missing required columns: "
                        f"{', '.join(missing_headers)}"
                    ),
                )

            for excel_row_num, row_vals in enumerate(row_iter, start=2):
                if row_vals is None:
                    continue
                row_dict: dict[str, Any] = {}
                is_completely_blank = True
                for col_name in IMPORT_COLUMNS:
                    col_idx = header_map.get(col_name)
                    cell_val = (
                        row_vals[col_idx]
                        if (col_idx is not None and col_idx < len(row_vals))
                        else None
                    )
                    if cell_val is not None and str(cell_val).strip() != "":
                        is_completely_blank = False
                    row_dict[col_name] = cell_val

                if is_completely_blank:
                    continue

                parsed_rows.append((excel_row_num, row_dict))
                if len(parsed_rows) > MAX_IMPORT_ROWS:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"IMPORT_ROW_LIMIT_EXCEEDED: Workbook exceeds maximum allowed "
                            f"{MAX_IMPORT_ROWS} non-empty rows."
                        ),
                    )
        finally:
            wb.close()

        if not parsed_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="IMPORT_FILE_EMPTY: Workbook contains no non-empty employee rows.",
            )

        # Load existing tenant departments & employees from PostgreSQL
        dept_rows = (
            await session.execute(
                select(Department.id, Department.name).where(Department.tenant_id == t_id)
            )
        ).all()
        dept_by_lower: dict[str, tuple[UUID, str]] = {
            str(name).strip().lower(): (d_id, str(name).strip()) for d_id, name in dept_rows
        }

        emp_rows = (
            await session.execute(
                select(Employee.id, Employee.email).where(Employee.tenant_id == t_id)
            )
        ).all()
        existing_emp_by_email: dict[str, UUID] = {
            str(email).strip().lower(): e_id for e_id, email in emp_rows
        }

        # Pre-count normalized email occurrences inside the uploaded file
        file_email_counts: dict[str, int] = {}
        for _, raw in parsed_rows:
            em = str(raw.get("email") or "").strip().lower()
            if em:
                file_email_counts[em] = file_email_counts.get(em, 0) + 1

        batch_id = uuid.uuid4().hex
        all_errors: list[ImportRowError] = []
        row_errors_map: dict[int, list[str]] = {r_num: [] for r_num, _ in parsed_rows}
        normalized_rows: dict[int, dict[str, Any]] = {}

        def _add_err(r_num: int, field: str, msg: str) -> None:
            all_errors.append(ImportRowError(row_number=r_num, field=field, message=msg))
            row_errors_map[r_num].append(f"{field}: {msg}")

        # ----------------------------------------------------------------------
        # Pass A: Deterministic Per-Row Validation (Rules 1, 2, 3, 4, 6)
        # ----------------------------------------------------------------------
        for r_num, raw in parsed_rows:
            full_name = str(raw.get("full_name") or "").strip()
            email_norm = str(raw.get("email") or "").strip().lower()
            phone_str = str(raw.get("phone") or "").strip() or None
            dept_raw = str(raw.get("department") or "").strip()
            dept_lower = dept_raw.lower()
            manager_email_norm = str(raw.get("manager_email") or "").strip().lower() or None
            job_title = str(raw.get("job_title") or "").strip()
            emp_type_norm = str(raw.get("employment_type") or "").strip().lower()
            join_date_display, join_date_iso = _normalize_date_cell(raw.get("join_date"))
            cnic_raw = str(raw.get("cnic") or "").strip() or None
            bank_raw = str(raw.get("bank_account") or "").strip() or None

            # 1. Required fields check
            if not full_name:
                _add_err(r_num, "full_name", "Required field 'full_name' is missing.")
            if not email_norm:
                _add_err(r_num, "email", "Required field 'email' is missing.")
            if not dept_raw:
                _add_err(r_num, "department", "Required field 'department' is missing.")
            if not job_title:
                _add_err(r_num, "job_title", "Required field 'job_title' is missing.")
            if not emp_type_norm:
                _add_err(r_num, "employment_type", "Required field 'employment_type' is missing.")
            if not join_date_display:
                _add_err(r_num, "join_date", "Required field 'join_date' is missing.")

            # 2. Email format, employment_type enum, ISO join_date
            if email_norm and not EMAIL_REGEX.match(email_norm):
                _add_err(r_num, "email", f"Invalid email format '{email_norm}'.")

            if emp_type_norm and emp_type_norm not in VALID_EMPLOYMENT_TYPES:
                _add_err(
                    r_num,
                    "employment_type",
                    (
                        f"Invalid employment_type '{emp_type_norm}'. "
                        f"Allowed: {', '.join(VALID_EMPLOYMENT_TYPES)}."
                    ),
                )

            if join_date_display and not join_date_iso:
                _add_err(
                    r_num,
                    "join_date",
                    f"Invalid join_date '{join_date_display}'. Expected ISO format YYYY-MM-DD.",
                )

            # 3. Duplicate email in file OR existing employee in DB
            if email_norm:
                if file_email_counts.get(email_norm, 0) > 1:
                    _add_err(
                        r_num,
                        "email",
                        f"Duplicate email '{email_norm}' appears multiple times in uploaded file.",
                    )
                if email_norm in existing_emp_by_email:
                    _add_err(
                        r_num,
                        "email",
                        f"Email '{email_norm}' already exists in tenant employee directory.",
                    )

            # 4. Department exists in tenant
            resolved_dept_id: str | None = None
            resolved_dept_name = dept_raw
            if dept_lower:
                dept_match = dept_by_lower.get(dept_lower)
                if not dept_match:
                    _add_err(
                        r_num,
                        "department",
                        f"Department '{dept_raw}' does not exist in this tenant.",
                    )
                else:
                    resolved_dept_id = str(dept_match[0])
                    resolved_dept_name = dept_match[1]

            # 6. Optional CNIC format validation (XXXXX-XXXXXXX-X or 13 digits)
            if cnic_raw and not CNIC_REGEX.match(cnic_raw):
                _add_err(
                    r_num,
                    "cnic",
                    "Invalid CNIC format. Expected 13-digit Pakistani CNIC (XXXXX-XXXXXXX-X or 13 digits).",
                )

            normalized_rows[r_num] = {
                "row_number": r_num,
                "full_name": full_name,
                "email": email_norm,
                "phone": phone_str,
                "department": resolved_dept_name,
                "department_id": resolved_dept_id,
                "manager_email": manager_email_norm,
                "job_title": job_title,
                "employment_type": emp_type_norm,
                "join_date": join_date_iso or join_date_display,
                "cnic_plain": cnic_raw,
                "bank_plain": bank_raw,
                "cnic_masked": EmployeeService.mask_cnic(cnic_raw),
                "bank_account_masked": EmployeeService.mask_bank_account(bank_raw),
            }

        # ----------------------------------------------------------------------
        # Pass B: Manager Reference & Circular Loop Validation (Rule 5)
        # ----------------------------------------------------------------------
        # Map unique batch email -> row_number for rows in the uploaded file
        batch_email_to_row: dict[str, int] = {}
        for r_num, nrow in normalized_rows.items():
            em = nrow["email"]
            if em and file_email_counts.get(em, 0) == 1:
                batch_email_to_row[em] = r_num

        # 5a. Direct manager_email existence & self-reference checks
        for r_num, nrow in normalized_rows.items():
            mgr_email = nrow["manager_email"]
            if not mgr_email:
                continue
            own_email = nrow["email"]
            if own_email and mgr_email == own_email:
                _add_err(
                    r_num,
                    "manager_email",
                    f"Employee '{own_email}' cannot be their own manager (self-reference).",
                )
                continue

            in_db = mgr_email in existing_emp_by_email
            in_batch_row = batch_email_to_row.get(mgr_email)
            if not in_db and in_batch_row is None:
                _add_err(
                    r_num,
                    "manager_email",
                    f"Manager email '{mgr_email}' not found in existing employees or uploaded batch.",
                )

        # 5b. Detect circular manager loops among batch rows (e.g. A -> B -> A)
        for r_num, nrow in normalized_rows.items():
            mgr_email = nrow["manager_email"]
            own_email = nrow["email"]
            if not mgr_email or not own_email or mgr_email == own_email:
                continue
            visited_emails: set[str] = {own_email}
            curr_mgr: str | None = mgr_email
            has_cycle = False
            while curr_mgr and curr_mgr in batch_email_to_row:
                if curr_mgr in visited_emails:
                    has_cycle = True
                    break
                visited_emails.add(curr_mgr)
                parent_row_num = batch_email_to_row[curr_mgr]
                curr_mgr = normalized_rows[parent_row_num]["manager_email"]
            if has_cycle:
                _add_err(
                    r_num,
                    "manager_email",
                    f"Circular manager hierarchy detected involving '{own_email}' and '{mgr_email}'.",
                )

        # 5c. If a row points to an in-batch manager row that itself has validation errors, flag it
        #      iteratively so invalid manager chains cannot be partially valid.
        changed = True
        while changed:
            changed = False
            for r_num, nrow in normalized_rows.items():
                if row_errors_map[r_num]:
                    continue
                mgr_email = nrow["manager_email"]
                if (
                    mgr_email
                    and mgr_email not in existing_emp_by_email
                    and mgr_email in batch_email_to_row
                ):
                    target_r_num = batch_email_to_row[mgr_email]
                    if row_errors_map[target_r_num]:
                        _add_err(
                            r_num,
                            "manager_email",
                            f"Manager '{mgr_email}' (row {target_r_num}) has validation errors.",
                        )
                        changed = True

        # ----------------------------------------------------------------------
        # Build Preview Rows & Encrypted Redis Staging Payload
        # ----------------------------------------------------------------------
        preview_rows: list[ImportPreviewRow] = []
        staged_rows: list[dict[str, Any]] = []
        valid_count = 0
        invalid_count = 0

        for r_num, _ in parsed_rows:
            nrow = normalized_rows[r_num]
            errs = row_errors_map[r_num]
            is_valid = len(errs) == 0
            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1

            preview_rows.append(
                ImportPreviewRow(
                    row_number=r_num,
                    full_name=nrow["full_name"],
                    email=nrow["email"],
                    phone=nrow["phone"],
                    department=nrow["department"],
                    manager_email=nrow["manager_email"],
                    job_title=nrow["job_title"],
                    employment_type=nrow["employment_type"],
                    join_date=nrow["join_date"],
                    cnic_masked=nrow["cnic_masked"],
                    bank_account_masked=nrow["bank_account_masked"],
                    is_valid=is_valid,
                    errors=errs,
                )
            )

            cnic_staging_enc: str | None = None
            if nrow["cnic_plain"] and is_valid:
                cnic_staging_enc = encrypt_field(
                    nrow["cnic_plain"],
                    _staging_aad_context(t_id, batch_id, r_num, "cnic"),
                )
            bank_staging_enc: str | None = None
            if nrow["bank_plain"] and is_valid:
                bank_staging_enc = encrypt_field(
                    nrow["bank_plain"],
                    _staging_aad_context(t_id, batch_id, r_num, "bank_account"),
                )

            staged_rows.append(
                {
                    "row_number": r_num,
                    "full_name": nrow["full_name"],
                    "email": nrow["email"],
                    "phone": nrow["phone"],
                    "department": nrow["department"],
                    "department_id": nrow["department_id"],
                    "manager_email": nrow["manager_email"],
                    "job_title": nrow["job_title"],
                    "employment_type": nrow["employment_type"],
                    "join_date": nrow["join_date"],
                    "cnic_masked": nrow["cnic_masked"],
                    "bank_account_masked": nrow["bank_account_masked"],
                    "cnic_staging_encrypted": cnic_staging_enc,
                    "bank_staging_encrypted": bank_staging_enc,
                    "is_valid": is_valid,
                    "errors": errs,
                }
            )

        staging_payload: dict[str, Any] = {
            "import_batch_id": batch_id,
            "tenant_id": str(t_id),
            "file_name": clean_filename,
            "total_rows": len(parsed_rows),
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "rows": staged_rows,
        }
        await _save_staging_payload(t_id, batch_id, staging_payload)

        return ImportValidationResult(
            import_batch_id=batch_id,
            file_name=clean_filename,
            total_rows=len(parsed_rows),
            valid_count=valid_count,
            invalid_count=invalid_count,
            can_commit=(invalid_count == 0 and valid_count > 0),
            preview_rows=preview_rows,
            errors=all_errors,
        )

    @staticmethod
    async def download_error_report_xlsx(
        *,
        tenant_id: UUID | str,
        import_batch_id: str,
    ) -> bytes:
        """
        Generates a downloadable `.xlsx` error report containing `row_number`, the 10 template
        columns (with CNIC and bank account masked and formula injection neutralized), and
        `validation_errors` for all invalid rows in the tenant's staged batch.
        """
        t_id = UUID(str(tenant_id))
        clean_batch_id = (import_batch_id or "").strip()
        staging = await _load_staging_payload(t_id, clean_batch_id)
        if not staging:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"IMPORT_BATCH_NOT_FOUND: Import batch '{clean_batch_id}' not found or expired.",
            )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Validation Errors"

        headers = ["row_number", *IMPORT_COLUMNS, "validation_errors"]
        header_fill = PatternFill(start_color="102C54", end_color="102C54", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, name="Calibri", size=11)
        error_row_fill = PatternFill(start_color="FCE8E6", end_color="FCE8E6", fill_type="solid")
        error_text_font = Font(color="B31412", bold=True, name="Calibri", size=10)

        for col_idx, h_name in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h_name)
            cell.fill = header_fill
            cell.font = header_font
            ws.column_dimensions[get_column_letter(col_idx)].width = max(len(h_name) + 6, 18)
        ws.column_dimensions[get_column_letter(len(headers))].width = 64
        ws.freeze_panes = "A2"

        out_row_idx = 2
        for srow in staging.get("rows", []):
            if srow.get("is_valid", True):
                continue
            row_values = [
                int(srow.get("row_number", 0)),
                _sanitize_excel_cell(srow.get("full_name")),
                _sanitize_excel_cell(srow.get("email")),
                _sanitize_excel_cell(srow.get("phone")),
                _sanitize_excel_cell(srow.get("department")),
                _sanitize_excel_cell(srow.get("manager_email")),
                _sanitize_excel_cell(srow.get("job_title")),
                _sanitize_excel_cell(srow.get("employment_type")),
                _sanitize_excel_cell(srow.get("join_date")),
                _sanitize_excel_cell(srow.get("cnic_masked")),
                _sanitize_excel_cell(srow.get("bank_account_masked")),
                _sanitize_excel_cell("; ".join(srow.get("errors") or [])),
            ]
            for col_idx, val in enumerate(row_values, start=1):
                c = ws.cell(row=out_row_idx, column=col_idx)
                if isinstance(val, str):
                    c.value = val
                    c.data_type = "s"
                else:
                    c.value = val
                c.fill = error_row_fill
                if col_idx == len(headers):
                    c.font = error_text_font
            out_row_idx += 1

        buf = io.BytesIO()
        try:
            wb.save(buf)
        finally:
            wb.close()
        return buf.getvalue()

    @staticmethod
    async def commit_import_batch(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        import_batch_id: str,
        actor_id: UUID | str | None = None,
    ) -> ImportCommitResponse:
        """
        Executes two-phase commit for a clean staged import batch:
        - Acquires tenant-scoped Redis NX commit lock (HTTP 409 on concurrent double-click).
        - Rejects with HTTP 400 if invalid_count > 0 or valid_count == 0.
        - Two-pass insert inside a single transaction:
          Pass 1: Pre-generates `emp_id = uuid.uuid4()`, decrypts staging PII and re-encrypts
                  via `EmployeeService.encrypt_cnic` / `encrypt_bank_account` bound to
                  canonical 4-field AAD `(tenant_id, "employee", emp_id, field_name)`.
          Pass 2: Resolves and links `manager_id` across existing DB employees and newly inserted
                  batch employees.
        - Enqueues `sheets_sync_queue`, writes `audit_logs`, commits, and clears the Redis staging key.
        """
        t_id = UUID(str(tenant_id))
        clean_batch_id = (import_batch_id or "").strip()

        if not await _acquire_commit_lock(t_id, clean_batch_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"IMPORT_BATCH_COMMIT_IN_PROGRESS: Commit for batch '{clean_batch_id}' "
                    "is already in progress."
                ),
            )

        try:
            staging = await _load_staging_payload(t_id, clean_batch_id)
            if not staging:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"IMPORT_BATCH_NOT_FOUND: Import batch '{clean_batch_id}' "
                        "not found, expired, or already committed."
                    ),
                )

            invalid_count = int(staging.get("invalid_count", 0))
            valid_count = int(staging.get("valid_count", 0))
            if invalid_count > 0 or valid_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"IMPORT_BATCH_HAS_ERRORS: Cannot commit batch '{clean_batch_id}' with "
                        f"{invalid_count} invalid row(s) and {valid_count} valid row(s)."
                    ),
                )

            rows: list[dict[str, Any]] = staging.get("rows", [])

            # Re-verify current tenant employees & departments inside the commit transaction
            existing_emp_rows = (
                await session.execute(
                    select(Employee.id, Employee.email).where(Employee.tenant_id == t_id)
                )
            ).all()
            email_to_emp_id: dict[str, UUID] = {
                str(email).strip().lower(): e_id for e_id, email in existing_emp_rows
            }

            dept_rows = (
                await session.execute(
                    select(Department.id, Department.name).where(Department.tenant_id == t_id)
                )
            ).all()
            dept_by_lower: dict[str, tuple[UUID, str]] = {
                str(name).strip().lower(): (d_id, str(name).strip()) for d_id, name in dept_rows
            }

            # ------------------------------------------------------------------
            # Pass 1: Insert Employees with Pre-Generated UUIDs & 4-Field AAD PII
            # ------------------------------------------------------------------
            inserted_employees: list[tuple[Employee, str | None, str]] = []
            base_count = len(email_to_emp_id)

            for idx, srow in enumerate(rows, start=1):
                r_num = int(srow["row_number"])
                email_norm = str(srow["email"]).strip().lower()
                if email_norm in email_to_emp_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"EMPLOYEE_EMAIL_CONFLICT: Email '{email_norm}' was concurrently "
                            "created in this tenant before commit."
                        ),
                    )

                dept_lower = str(srow["department"]).strip().lower()
                dept_info = dept_by_lower.get(dept_lower)
                if not dept_info:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Department '{srow['department']}' no longer exists in this tenant.",
                    )
                dept_id, dept_name = dept_info

                # Pre-generate emp_id before encrypting CNIC / bank_account
                emp_id = uuid.uuid4()
                emp_code = f"IMP-{base_count + idx:04d}"

                cnic_enc: str | None = None
                if srow.get("cnic_staging_encrypted"):
                    plain_cnic = decrypt_field(
                        srow["cnic_staging_encrypted"],
                        _staging_aad_context(t_id, clean_batch_id, r_num, "cnic"),
                    )
                    cnic_enc = EmployeeService.encrypt_cnic(
                        tenant_id=t_id,
                        employee_id=emp_id,
                        plaintext_cnic=plain_cnic,
                    )

                bank_enc: str | None = None
                if srow.get("bank_staging_encrypted"):
                    plain_bank = decrypt_field(
                        srow["bank_staging_encrypted"],
                        _staging_aad_context(t_id, clean_batch_id, r_num, "bank_account"),
                    )
                    bank_enc = EmployeeService.encrypt_bank_account(
                        tenant_id=t_id,
                        employee_id=emp_id,
                        plaintext_bank_account=plain_bank,
                    )

                emp = Employee(
                    id=emp_id,
                    tenant_id=t_id,
                    employee_code=emp_code,
                    full_name=str(srow["full_name"]).strip(),
                    email=email_norm,
                    phone=srow.get("phone"),
                    department_id=dept_id,
                    manager_id=None,
                    job_title=str(srow["job_title"]).strip(),
                    employment_type=str(srow["employment_type"]).strip().lower(),
                    join_date=date.fromisoformat(str(srow["join_date"])),
                    status="active",
                    cnic_encrypted=cnic_enc,
                    bank_account_encrypted=bank_enc,
                )
                session.add(emp)
                email_to_emp_id[email_norm] = emp_id
                inserted_employees.append((emp, srow.get("manager_email"), dept_name))

            await session.flush()

            # ------------------------------------------------------------------
            # Pass 2: Link manager_id Across Existing & Newly Inserted Employees
            # ------------------------------------------------------------------
            for emp, mgr_email, _ in inserted_employees:
                if mgr_email:
                    mgr_norm = str(mgr_email).strip().lower()
                    target_mgr_id = email_to_emp_id.get(mgr_norm)
                    if not target_mgr_id:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Manager email '{mgr_norm}' could not be resolved during commit.",
                        )
                    emp.manager_id = target_mgr_id

            await session.flush()

            # Initialize leave balances & accrual ledger entries for all imported employees
            for emp, _, _ in inserted_employees:
                await initialize_employee_leave_balances(
                    session,
                    tenant_id=t_id,
                    employee_id=emp.id,
                    actor_id=actor_id,
                )

            # Enqueue newly committed employees into sheets_sync_queue
            sheets_queued = 0
            committed_ids: list[str] = []
            for emp, _, dept_name in inserted_employees:
                emp_id_str = str(emp.id)
                committed_ids.append(emp_id_str)
                await SheetsSyncService.enqueue_sync(
                    session=session,
                    tenant_id=t_id,
                    entity_type="employee",
                    entity_id=emp_id_str,
                    action="bulk_import",
                    payload={
                        "employee_id": emp.employee_code or emp_id_str[:8].upper(),
                        "name": emp.full_name,
                        "email": emp.email,
                        "department": dept_name,
                        "role": emp.job_title,
                        "status": emp.status,
                    },
                )
                sheets_queued += 1

            await write_audit(
                session,
                action="employee.bulk_imported",
                target_type="employee_import_batch",
                target_id=clean_batch_id,
                details={
                    "import_batch_id": clean_batch_id,
                    "file_name": staging.get("file_name"),
                    "committed_count": len(committed_ids),
                    "sheets_queued": sheets_queued,
                },
                actor_id=actor_id,
                tenant_id=t_id,
            )

            await session.commit()
            await _delete_staging_payload(t_id, clean_batch_id)

            return ImportCommitResponse(
                status="committed",
                import_batch_id=clean_batch_id,
                committed_count=len(committed_ids),
                sheets_queued=sheets_queued,
                employee_ids=committed_ids,
            )
        finally:
            await _release_commit_lock(t_id, clean_batch_id)
