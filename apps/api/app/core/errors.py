"""
Global Exception Handlers and Error Formatting for Agentic HR Core.
Ensures all HTTP errors, validation errors, and unexpected exceptions return a consistent JSON schema.
"""

import logging
from typing import Any

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.middleware import request_id_ctx
from app.schemas.errors import ErrorDetail, ErrorResponse

logger = logging.getLogger("agentic_hr.errors")

STATUS_CODE_MAP = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_SERVER_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


def _resolve_request_id(request: Request) -> str | None:
    """Resolve request_id from request.state or contextvar."""
    return getattr(request.state, "request_id", None) or request_id_ctx.get(None)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = _resolve_request_id(request)
    code = STATUS_CODE_MAP.get(exc.status_code, f"HTTP_{exc.status_code}")
    message = str(exc.detail) if exc.detail else "An HTTP error occurred."

    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            details=None,
            request_id=request_id,
        )
    ).model_dump()

    headers = dict(exc.headers) if getattr(exc, "headers", None) else {}
    if request_id:
        headers["X-Request-ID"] = request_id
    return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = _resolve_request_id(request)

    # Format Pydantic errors into clean field issues
    formatted_errors: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = " -> ".join(str(loc_item) for loc_item in err.get("loc", []))
        formatted_errors.append(
            {
                "field": loc,
                "message": err.get("msg"),
                "type": err.get("type"),
            }
        )

    payload = ErrorResponse(
        error=ErrorDetail(
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details=formatted_errors,
            request_id=request_id,
        )
    ).model_dump()

    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=payload,
        headers=headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _resolve_request_id(request)
    logger.exception(f"Unhandled exception during request processing: {exc}")

    payload = ErrorResponse(
        error=ErrorDetail(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected server error occurred.",
            details=None,
            request_id=request_id,
        )
    ).model_dump()

    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=payload,
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all unified error handlers with the FastAPI application."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
