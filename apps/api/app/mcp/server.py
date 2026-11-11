"""
Unified FastMCP Server for Agentic HR Core.
Aggregates all 13 Google Workspace connector tools into a single authoritative server
for autonomous AI agents (DocuVerify, Screener, Code Tester, Policy Brain).
"""

from app.mcp.base import FastMCP
from app.mcp.calendar_mcp import (
    calendar_mcp_server,
    cancel_calendar_event_tool,
    create_calendar_event_tool,
    get_calendar_free_busy_tool,
    update_calendar_event_tool,
)
from app.mcp.drive_mcp import (
    download_drive_file_tool,
    drive_mcp_server,
    ensure_drive_folder_tool,
    list_drive_files_tool,
    upload_drive_file_tool,
)
from app.mcp.gmail_mcp import (
    gmail_mcp_server,
    list_sent_messages_tool,
    send_email_tool,
)
from app.mcp.sheets_mcp import (
    read_sheets_range_tool,
    reconcile_sheets_drift_tool,
    sheets_mcp_server,
    sync_sheets_tool,
)

# Unified FastMCP server instance
unified_mcp_server = FastMCP(
    name="agentic-hr-core",
    instructions=(
        "Unified Agentic HR MCP Server providing strictly guarded tools for Gmail, "
        "Google Sheets, Google Drive, and Google Calendar. Enforces server-bound context, "
        "RBAC, Read-Only mode, and Rule 6 Untrusted-Data defense on every invocation."
    ),
)

ALL_MCP_TOOLS = [
    # Gmail
    send_email_tool,
    list_sent_messages_tool,
    # Sheets
    sync_sheets_tool,
    read_sheets_range_tool,
    reconcile_sheets_drift_tool,
    # Drive
    ensure_drive_folder_tool,
    upload_drive_file_tool,
    download_drive_file_tool,
    list_drive_files_tool,
    # Calendar
    get_calendar_free_busy_tool,
    create_calendar_event_tool,
    update_calendar_event_tool,
    cancel_calendar_event_tool,
]

for tool_fn in ALL_MCP_TOOLS:
    unified_mcp_server.add_tool(tool_fn)

MODULAR_MCP_SERVERS = {
    "gmail": gmail_mcp_server,
    "sheets": sheets_mcp_server,
    "drive": drive_mcp_server,
    "calendar": calendar_mcp_server,
    "unified": unified_mcp_server,
}
