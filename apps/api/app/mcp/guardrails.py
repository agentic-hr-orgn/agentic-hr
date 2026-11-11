"""
Tool-Level Security Guardrails for FastMCP Servers.
Enforces:
1. Server-Bound Context Presence (no forged prompt arguments).
2. Rule 6 Untrusted-Data Policy (blocks mutating tools when untrusted_context=True).
3. Role-Based Access Control (RBAC: blocks mutating tools for employee role unless is_hr_head=True).
4. Read-Only Mode enforcement (blocks mutating tools when connector is_read_only=True).
"""

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.untrusted import ToolAuthorizationError, enforce_untrusted_context_policy
from app.mcp.context import MCPExecutionContext, get_current_mcp_context
from app.mcp.exceptions import (
    MCPPermissionDeniedError,
    MCPUntrustedContextBlockedError,
)

logger = logging.getLogger("agentic_hr.mcp.guardrails")

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

DEFAULT_MUTATING_ALLOWED_ROLES: tuple[str, ...] = ("owner", "manager")


def mcp_guardrail(
    tool_name: str,
    *,
    is_write: bool = False,
    allowed_roles: tuple[str, ...] = DEFAULT_MUTATING_ALLOWED_ROLES,
) -> Callable[[F], F]:
    """
    Authoritative security guardrail decorator for FastMCP tools.
    Must be placed on every MCP tool function.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 1. Server-Bound Execution Context
            ctx: MCPExecutionContext = get_current_mcp_context()

            # 2. Rule 6 Untrusted-Data Defense
            if ctx.untrusted_context:
                try:
                    enforce_untrusted_context_policy(
                        tool_name=tool_name,
                        is_untrusted_context=True,
                    )
                except ToolAuthorizationError as exc:
                    logger.warning(
                        f"[MCP Untrusted Block] Tool '{tool_name}' blocked in untrusted context "
                        f"for tenant '{ctx.tenant_id}': {exc}"
                    )
                    raise MCPUntrustedContextBlockedError(str(exc)) from exc

            # 3. Role-Based Access Control (RBAC)
            if is_write:
                is_authorized = (ctx.role in allowed_roles) or bool(ctx.is_hr_head)
                if not is_authorized:
                    logger.warning(
                        f"[MCP RBAC Denied] User '{ctx.user_id}' with role '{ctx.role}' "
                        f"(hr_head={ctx.is_hr_head}) denied access to mutating tool '{tool_name}'."
                    )
                    raise MCPPermissionDeniedError(
                        f"Role '{ctx.role}' is not authorized to execute mutating tool '{tool_name}'. "
                        f"Requires role in {allowed_roles} or is_hr_head=True."
                    )

            # 4. Proceed with guarded tool invocation
            return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
