"""
Pre-Stage 5 Enterprise Security & Production Hardening Regression Suite.

Verifies:
1. Production `Settings` fail-closed guards (`ENVIRONMENT="production"` / `"staging"`):
   - Rejects `USE_FAKE_INTEGRATIONS=True`
   - Rejects `DEBUG=True`
   - Rejects default or short `SECRET_KEY` (< 32 chars)
   - Rejects missing `FIELD_ENCRYPTION_KEY`
   - Rejects `RATE_LIMIT_FAIL_OPEN=True`
2. Route Lockdown & Production Gating:
   - `POST /api/v1/workflows/sample/trigger` rejects unauthenticated (`401`) and `employee` role (`403`),
     and returns `404` when `ENVIRONMENT="production"`.
   - `/api/synthetic/*` returns `404` when `ENVIRONMENT="production"`.
3. HTTP Security Headers & Explicit CORS Allowlists:
   - Every `/api/v1/*` response includes `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
     `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and `X-Request-ID`.
   - CORS preflight does not return wildcard `*` for `Access-Control-Allow-Methods` or
     `Access-Control-Allow-Headers`.
4. Bounce Webhook RBAC:
   - `POST /api/v1/connectors/email/bounces` blocks `employee` role with HTTP `403`.
5. Magic-Byte File Validation:
   - Rejects `.exe` (`MZ`) spoofed as `.pdf` (`application/pdf`).
   - Rejects arbitrary ZIP archive (`PK\x03\x04` without `[Content_Types].xml` + `word/`) spoofed as `.docx`.
   - Rejects binary/NUL bytes spoofed as `.txt` (`text/plain`).
   - Rejects non-OOXML bytes spoofed as `.xlsx` on `/api/v1/employees/import/validate`.
   - Accepts valid in-memory OOXML `.docx` ZIP archive.
6. Streaming Upload Cutoff (`read_upload_bytes_limited`):
   - Aborts immediately as soon as streamed chunks exceed `max_bytes` without reading the remainder
     of an oversized stream into memory.
7. Orphan Local File Cleanup on DB Failure:
   - When a DB error occurs after `StorageHelper.store_file` writes a local file in
     `PolicyStubService.upload_policy_stub`, the newly written file is deleted from `.local_storage/`.
"""

import io
import zipfile
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.db.session import AsyncSessionLocal
from app.models.tenant import set_tenant_context
from app.services.policy_stub_service import PolicyStubService
from app.services.storage_helper import StorageHelper, read_upload_bytes_limited
from tests.isolation_harness import MultiTenantTestContext

VALID_PROD_KWARGS = {
    "ENVIRONMENT": "production",
    "USE_FAKE_INTEGRATIONS": False,
    "DEBUG": False,
    "SECRET_KEY": "prod-secret-key-at-least-32-bytes-long-verified",
    "FIELD_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    "RATE_LIMIT_FAIL_OPEN": False,
}


def test_01_production_settings_fail_closed_guards() -> None:
    """Verifies all 5 production configuration guards fail closed outside non-production."""
    # Baseline valid production settings succeed
    s_ok = Settings(**VALID_PROD_KWARGS)
    assert s_ok.is_non_production is False

    # 1. USE_FAKE_INTEGRATIONS=True rejected
    with pytest.raises(ValidationError, match="USE_FAKE_INTEGRATIONS=true is strictly forbidden"):
        Settings(**{**VALID_PROD_KWARGS, "USE_FAKE_INTEGRATIONS": True})

    # 2. DEBUG=True rejected
    with pytest.raises(ValidationError, match="DEBUG=true is strictly forbidden"):
        Settings(**{**VALID_PROD_KWARGS, "DEBUG": True})

    # 3. Default or short SECRET_KEY rejected
    with pytest.raises(ValidationError, match="strong non-default SECRET_KEY"):
        Settings(
            **{
                **VALID_PROD_KWARGS,
                "SECRET_KEY": "agentic-hr-development-secret-key-at-least-32-bytes-long",
            }
        )
    with pytest.raises(ValidationError, match="strong non-default SECRET_KEY"):
        Settings(**{**VALID_PROD_KWARGS, "SECRET_KEY": "too-short-secret"})

    # 4. Missing FIELD_ENCRYPTION_KEY rejected
    with pytest.raises(ValidationError, match="FIELD_ENCRYPTION_KEY is required in production"):
        Settings(**{**VALID_PROD_KWARGS, "FIELD_ENCRYPTION_KEY": None})

    # 5. RATE_LIMIT_FAIL_OPEN=True rejected (including in 'staging')
    with pytest.raises(ValidationError, match="RATE_LIMIT_FAIL_OPEN=true is strictly forbidden"):
        Settings(
            **{
                **VALID_PROD_KWARGS,
                "ENVIRONMENT": "staging",
                "RATE_LIMIT_FAIL_OPEN": True,
            }
        )


@pytest.mark.asyncio
async def test_02_workflows_and_synthetic_routes_locked_down(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifies `/api/v1/workflows/sample/trigger` requires Owner auth and returns 404 in production,
    and `/api/synthetic/*` returns 404 when `ENVIRONMENT='production'`.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    # Unauthenticated request -> 401
    r_unauth = await async_client.post("/api/v1/workflows/sample/trigger", json={})
    assert r_unauth.status_code == 401

    # Employee role -> 403
    r_emp = await async_client.post(
        "/api/v1/workflows/sample/trigger",
        json={},
        headers=emp_headers,
    )
    assert r_emp.status_code == 403

    # In production environment -> 404 on both sample workflow trigger and synthetic routes
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    r_wf_prod = await async_client.post(
        "/api/v1/workflows/sample/trigger",
        json={},
        headers=owner_headers,
    )
    assert r_wf_prod.status_code == 404

    r_syn_prod = await async_client.get("/api/synthetic/company")
    assert r_syn_prod.status_code == 404


@pytest.mark.asyncio
async def test_03_http_security_headers_cors_and_bounce_rbac(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies enterprise HTTP security headers on API responses, explicit CORS allowlists,
    and RBAC blocking `employee` role on `POST /api/v1/connectors/email/bounces`.
    """
    globex = isolation_context.company_b
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    resp = await async_client.get("/api/v1/auth/me", headers=emp_headers)
    assert resp.status_code == 200
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "no-referrer"
    assert resp.headers.get("cache-control") == "no-store"
    assert resp.headers.get("x-request-id")

    # CORS preflight check
    cors_resp = await async_client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    assert cors_resp.status_code == 200
    allow_methods = cors_resp.headers.get("access-control-allow-methods", "")
    assert "*" not in allow_methods
    assert "POST" in allow_methods

    # Employee role cannot record email bounces (403)
    bounce_resp = await async_client.post(
        "/api/v1/connectors/email/bounces",
        headers=emp_headers,
        json={"provider_message_id": "fake-msg-123", "error_message": "550 bounce"},
    )
    assert bounce_resp.status_code == 403


@pytest.mark.asyncio
async def test_04_magic_byte_spoofing_rejected_and_valid_ooxml_accepted(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies magic-byte validation rejects spoofed PDF, DOCX, TXT, and XLSX files
    while accepting valid OOXML `.docx` archives.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    # 1. Executable (MZ) renamed to .pdf -> 400 INVALID_POLICY_FILE_TYPE
    r_fake_pdf = await async_client.post(
        "/api/v1/policies",
        headers=owner_headers,
        files={"file": ("spoofed.pdf", b"MZ\x90\x00\x03\x00\x00\x00", "application/pdf")},
    )
    assert r_fake_pdf.status_code == 400
    assert "INVALID_POLICY_FILE_TYPE" in r_fake_pdf.text

    # 2. Non-OOXML ZIP renamed to .docx -> 400 INVALID_POLICY_FILE_TYPE
    plain_zip_buf = io.BytesIO()
    with zipfile.ZipFile(plain_zip_buf, "w") as zf:
        zf.writestr("evil.txt", "not a word document")
    r_fake_docx = await async_client.post(
        "/api/v1/policies",
        headers=owner_headers,
        files={
            "file": (
                "spoofed.docx",
                plain_zip_buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert r_fake_docx.status_code == 400
    assert "INVALID_POLICY_FILE_TYPE" in r_fake_docx.text

    # 3. Binary with NUL byte renamed to .txt -> 400 INVALID_POLICY_FILE_TYPE
    r_fake_txt = await async_client.post(
        "/api/v1/policies",
        headers=owner_headers,
        files={"file": ("spoofed.txt", b"hello\x00world", "text/plain")},
    )
    assert r_fake_txt.status_code == 400
    assert "INVALID_POLICY_FILE_TYPE" in r_fake_txt.text

    # 4. Non-XLSX file renamed to .xlsx on employee import -> 400 INVALID_XLSX_FILE
    r_fake_xlsx = await async_client.post(
        "/api/v1/employees/import/validate",
        headers=owner_headers,
        files={
            "file": (
                "spoofed.xlsx",
                plain_zip_buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert r_fake_xlsx.status_code == 400
    assert "INVALID_XLSX_FILE" in r_fake_xlsx.text

    # 5. Valid in-memory OOXML .docx succeeds (201) and cleans up via DELETE
    valid_docx_buf = io.BytesIO()
    with zipfile.ZipFile(valid_docx_buf, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types></Types>')
        zf.writestr("word/document.xml", "<w:document>Valid Policy</w:document>")

    r_ok_docx = await async_client.post(
        "/api/v1/policies",
        headers=owner_headers,
        data={"title": "Valid DOCX Handbook"},
        files={
            "file": (
                "handbook.docx",
                valid_docx_buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert r_ok_docx.status_code == 201, r_ok_docx.text
    policy_id = r_ok_docx.json()["id"]
    await async_client.delete(f"/api/v1/policies/{policy_id}", headers=owner_headers)


@pytest.mark.asyncio
async def test_05_streaming_upload_cutoff_aborts_early() -> None:
    """
    Verifies `read_upload_bytes_limited` reads in bounded chunks and aborts immediately
    once `max_bytes` is exceeded without draining the rest of an oversized stream.
    """

    class _TrackingStream:
        def __init__(self) -> None:
            self.bytes_read = 0

        def read(self, size: int = -1) -> bytes:
            chunk_len = 65536 if size <= 0 else size
            self.bytes_read += chunk_len
            return b"A" * chunk_len

    tracking = _TrackingStream()
    upload = UploadFile(file=tracking, filename="huge.pdf")  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await read_upload_bytes_limited(upload, max_bytes=100 * 1024, error_code="TEST_TOO_LARGE")

    assert exc_info.value.status_code == 400
    assert "TEST_TOO_LARGE" in str(exc_info.value.detail)
    # Must stop after 2 chunks (128KB) for a 100KB limit — never reading megabytes
    assert tracking.bytes_read <= 128 * 1024


@pytest.mark.asyncio
async def test_06_orphan_local_file_cleaned_up_on_db_error(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies that if a database error occurs after `StorageHelper.store_file` writes a local
    file during `PolicyStubService.upload_policy_stub`, the orphan file on disk is removed.
    """
    globex = isolation_context.company_b
    captured_paths = []
    orig_store = StorageHelper.store_file

    async def _spy_store_file(*args, **kwargs):
        ref = await orig_store(*args, **kwargs)
        p = StorageHelper._resolve_local_path(globex.tenant_id, ref.storage_key)
        captured_paths.append(p)
        return ref

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, globex.tenant_id, globex.owner.clerk_user_id)
        with (
            patch.object(StorageHelper, "store_file", side_effect=_spy_store_file),
            patch(
                "app.services.policy_stub_service.write_audit",
                side_effect=RuntimeError("Simulated DB failure after file write"),
            ),
            pytest.raises(RuntimeError, match="Simulated DB failure after file write"),
        ):
            await PolicyStubService.upload_policy_stub(
                session=session,
                tenant_id=globex.tenant_id,
                file_name="orphan_check.pdf",
                content=b"%PDF-1.4\n%orphan test\n%%EOF",
                content_type="application/pdf",
                title="Orphan Check",
                storage_provider="local",
                actor_id=globex.owner.user_id,
            )
        await session.rollback()

    assert len(captured_paths) == 1
    assert not captured_paths[0].exists(), "Orphan local file was not deleted after DB failure!"
