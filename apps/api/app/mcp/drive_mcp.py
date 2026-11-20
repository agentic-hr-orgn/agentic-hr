"""
Google Drive FastMCP Server for Agentic HR Core.
Exposes ensure_drive_folder_tool, upload_drive_file_tool, download_drive_file_tool,
and list_drive_files_tool under ADR 006 drive.file least-privilege scope.
"""

import base64
from typing import Any

from app.core.timezones import iso_pkt
from app.mcp.base import FastMCP
from app.mcp.context import get_current_mcp_context, get_mcp_session
from app.mcp.guardrails import mcp_guardrail
from app.services.drive_service import DriveManagerService

drive_mcp_server = FastMCP(
    name="agentic-hr-drive",
    instructions="Google Drive connector tools for storing and retrieving candidate documents and policies strictly within per-tenant folders.",
)


@drive_mcp_server.tool(
    name="ensure_drive_folder_tool",
    description="Finds or creates a dedicated root folder in Google Drive for the active tenant under drive.file scope.",
)
@mcp_guardrail("ensure_drive_folder_tool", is_write=True)
async def ensure_drive_folder_tool(
    folder_name: str = "AgenticHR - Acme Tech Solutions",
    parent_folder_id: str | None = None,
) -> dict[str, str]:
    """
    Ensures a dedicated tenant folder exists in Google Drive.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        folder_id = await DriveManagerService.ensure_tenant_folder(
            session=session,
            tenant_id=ctx.tenant_id,
            folder_name=folder_name,
            parent_folder_id=parent_folder_id,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            "folder_id": folder_id,
            "folder_name": folder_name,
            "parent_folder_id": parent_folder_id or "",
        }


@drive_mcp_server.tool(
    name="upload_drive_file_tool",
    description="Uploads a file to Google Drive strictly inside the tenant folder with MIME validation and 10MB limit. Enforces idempotency and read-only checks.",
)
@mcp_guardrail("upload_drive_file_tool", is_write=True)
async def upload_drive_file_tool(
    name: str,
    content_base64: str,
    mime_type: str,
    folder_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """
    Uploads a file into the active tenant's Google Drive folder.
    """
    ctx = get_current_mcp_context()
    content_bytes = base64.b64decode(content_base64)
    async with get_mcp_session(ctx) as session:
        record = await DriveManagerService.upload_file(
            session=session,
            tenant_id=ctx.tenant_id,
            name=name,
            content=content_bytes,
            mime_type=mime_type,
            folder_id=folder_id,
            idempotency_key=idempotency_key,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            "id": str(record.id),
            "drive_file_id": record.drive_file_id,
            "name": record.name,
            "mime_type": record.mime_type,
            "size_bytes": record.size_bytes,
            "web_view_link": record.web_view_link,
            "folder_id": record.folder_id,
            "parent_folder_id": record.folder_id,
            "created_at": iso_pkt(record.created_at),
        }


@drive_mcp_server.tool(
    name="download_drive_file_tool",
    description="Downloads file bytes from Google Drive, verifying the file belongs to the active tenant. Returns base64 encoded content.",
)
@mcp_guardrail("download_drive_file_tool", is_write=False)
async def download_drive_file_tool(
    file_id: str,
) -> dict[str, Any]:
    """
    Downloads a tenant file from Google Drive.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        file_bytes, record = await DriveManagerService.download_file(
            session=session,
            tenant_id=ctx.tenant_id,
            file_id=file_id,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            "file_id": file_id,
            "name": record.name if record else None,
            "mime_type": record.mime_type if record else None,
            "size_bytes": len(file_bytes),
            "content_base64": base64.b64encode(file_bytes).decode("ascii"),
        }


@drive_mcp_server.tool(
    name="list_drive_files_tool",
    description="Lists files scoped strictly inside the active tenant's Drive folder under drive.file scope.",
)
@mcp_guardrail("list_drive_files_tool", is_write=False)
async def list_drive_files_tool(
    folder_id: str | None = None,
    page_size: int = 50,
) -> list[dict[str, Any]]:
    """
    Lists tenant-scoped files stored in Google Drive.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        records = await DriveManagerService.list_files(
            session=session,
            tenant_id=ctx.tenant_id,
            folder_id=folder_id,
            page_size=page_size,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return [
            {
                "id": str(r.id),
                "drive_file_id": r.drive_file_id,
                "name": r.name,
                "mime_type": r.mime_type,
                "size_bytes": r.size_bytes,
                "web_view_link": r.web_view_link,
                "folder_id": r.folder_id,
                "parent_folder_id": r.folder_id,
                "created_at": iso_pkt(r.created_at),
            }
            for r in records
        ]
