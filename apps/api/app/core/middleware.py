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

# Context variable accessible across async tasks during request lifetime
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

logger = logging.getLogger("agentic_hr.access")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that:
    1. Extracts or generates a unique X-Request-ID for every request.
    2. Stores the ID in request.state and Python contextvars for logging.
    3. Attaches the X-Request-ID to the outgoing response headers.
    4. Emits structured access logs with execution timing.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Check incoming header or generate new UUID4
        request_id = request.headers.get("X-Request-ID")
        if not request_id or not request_id.strip():
            request_id = uuid.uuid4().hex

        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
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
            request_id_ctx.reset(token)
