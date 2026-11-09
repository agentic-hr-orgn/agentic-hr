"""
Request ID and Context Middleware for Agentic HR Core.
Ensures every incoming HTTP request receives a unique identifier propagated via contextvars and headers.
"""

import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Context variables accessible across async tasks during request lifetime
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_ctx: ContextVar[str | None] = ContextVar("client_ip", default=None)
user_agent_ctx: ContextVar[str | None] = ContextVar("user_agent", default=None)

logger = logging.getLogger("agentic_hr.access")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that:
    1. Extracts or generates a unique X-Request-ID for every request.
    2. Captures client IP and user agent in contextvars for safe audit metadata.
    3. Stores the ID in request.state and Python contextvars for logging.
    4. Attaches the X-Request-ID to the outgoing response headers.
    5. Emits structured access logs with execution timing.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Check incoming header or generate new UUID4
        request_id = request.headers.get("X-Request-ID")
        if not request_id or not request_id.strip():
            request_id = uuid.uuid4().hex

        client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        user_agent = request.headers.get("user-agent", "unknown")

        request.state.request_id = request_id
        token_req = request_id_ctx.set(request_id)
        token_ip = client_ip_ctx.set(client_ip)
        token_ua = user_agent_ctx.set(user_agent)
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            response.headers["X-Request-ID"] = request_id

            logger.info(
                f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms}ms)",
                extra={
                    "extra_fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    }
                },
            )
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception(
                f"{request.method} {request.url.path} -> Unhandled Exception ({duration_ms}ms)",
                extra={
                    "extra_fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": 500,
                        "duration_ms": duration_ms,
                    }
                },
            )
            raise
        finally:
            request_id_ctx.reset(token_req)
            client_ip_ctx.reset(token_ip)
            user_agent_ctx.reset(token_ua)
