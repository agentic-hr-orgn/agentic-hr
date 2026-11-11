"""
Agentic HR FastMCP Subsystem.
Provides strictly guarded Model Context Protocol tools for autonomous AI agents.
"""

from app.mcp.base import FastMCP, create_fastmcp_server
from app.mcp.calendar_mcp import (
    calendar_mcp_server,
    cancel_calendar_event_tool,
    create_calendar_event_tool,
    get_calendar_free_busy_tool,
    update_calendar_event_tool,
)
from app.mcp.context import (
    MCPExecutionContext,
    get_current_mcp_context,
    get_mcp_session,
    mcp_execution_context,
)
from app.mcp.drive_mcp import (
    download_drive_file_tool,
    drive_mcp_server,
    ensure_drive_folder_tool,
    list_drive_files_tool,
    upload_drive_file_tool,
)
from app.mcp.exceptions import (
    MCPContextMissingError,
    MCPError,
    MCPPermissionDeniedError,
    MCPUntrustedContextBlockedError,
)
from app.mcp.gmail_mcp import (
    gmail_mcp_server,
    list_sent_messages_tool,
    send_email_tool,
)
from app.mcp.guardrails import mcp_guardrail
from app.mcp.server import (
    ALL_MCP_TOOLS,
    MODULAR_MCP_SERVERS,
    unified_mcp_server,
)
from app.mcp.sheets_mcp import (
    read_sheets_range_tool,
    reconcile_sheets_drift_tool,
    sheets_mcp_server,
    sync_sheets_tool,
)

__all__ = [
    "ALL_MCP_TOOLS",
    "MODULAR_MCP_SERVERS",
    "FastMCP",
    "MCPContextMissingError",
    "MCPError",
    "MCPExecutionContext",
    "MCPPermissionDeniedError",
    "MCPUntrustedContextBlockedError",
    "calendar_mcp_server",
    "cancel_calendar_event_tool",
    "create_calendar_event_tool",
    "create_fastmcp_server",
    "download_drive_file_tool",
    "drive_mcp_server",
    "ensure_drive_folder_tool",
    "get_calendar_free_busy_tool",
    "get_current_mcp_context",
    "get_mcp_session",
    "gmail_mcp_server",
    "list_drive_files_tool",
    "list_sent_messages_tool",
    "mcp_execution_context",
    "mcp_guardrail",
    "read_sheets_range_tool",
    "reconcile_sheets_drift_tool",
    "send_email_tool",
    "sheets_mcp_server",
    "sync_sheets_tool",
    "unified_mcp_server",
    "update_calendar_event_tool",
    "upload_drive_file_tool",
]
