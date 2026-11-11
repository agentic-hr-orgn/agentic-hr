"""
MCP Exceptions for Agentic HR Core.
Enforces security boundaries for Role-Based Access Control (RBAC),
Read-Only mode, Untrusted-Data defense, and missing execution contexts.
"""


class MCPError(Exception):
    """Base exception for all MCP tool runtime errors."""


class MCPContextMissingError(MCPError):
    """Raised when an MCP tool is invoked without a server-bound MCPExecutionContext."""


class MCPPermissionDeniedError(MCPError):
    """Raised when an actor's role does not possess permissions to execute an MCP tool."""


class MCPUntrustedContextBlockedError(MCPError):
    """Raised when a mutating MCP tool is invoked while in an untrusted-data context."""
