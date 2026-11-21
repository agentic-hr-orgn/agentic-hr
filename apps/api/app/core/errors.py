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
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


def _build_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _resolve_request_id(request)
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            details=None,
            request_id=request_id,
        )
    ).model_dump()
    resp_headers = dict(headers) if headers else {}
    if request_id:
        resp_headers["X-Request-ID"] = request_id
    return JSONResponse(status_code=status_code, content=payload, headers=resp_headers)


def register_error_handlers(app: FastAPI) -> None:
    """Register all unified error handlers with the FastAPI application."""
    from app.services.connector_service import (
        ConnectorAuthExpiredError,
        ConnectorRateLimitError,
        ConnectorServiceUnavailableError,
        CredentialNotFoundError,
        ReadOnlyConnectorError,
    )

    async def connector_auth_expired_handler(
        request: Request, exc: ConnectorAuthExpiredError
    ) -> JSONResponse:
        return _build_error_response(
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message=f"CONNECTOR_AUTH_EXPIRED: {exc}",
        )

    async def connector_rate_limit_handler(
        request: Request, exc: ConnectorRateLimitError
    ) -> JSONResponse:
        retry_after = max(1, int(getattr(exc, "retry_after", 1)))
        return _build_error_response(
            request,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="TOO_MANY_REQUESTS",
            message=f"CONNECTOR_RATE_LIMITED: {exc}",
            headers={"Retry-After": str(retry_after)},
        )

    async def connector_unavailable_handler(
        request: Request, exc: ConnectorServiceUnavailableError
    ) -> JSONResponse:
        return _build_error_response(
            request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            message=f"CONNECTOR_UNAVAILABLE: {exc}",
        )

    async def connector_read_only_handler(
        request: Request, exc: ReadOnlyConnectorError
    ) -> JSONResponse:
        return _build_error_response(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
            message=f"CONNECTOR_READ_ONLY: {exc}",
        )

    async def credential_not_found_handler(
        request: Request, exc: CredentialNotFoundError
    ) -> JSONResponse:
        return _build_error_response(
            request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="NOT_FOUND",
            message=str(exc),
        )

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(ConnectorAuthExpiredError, connector_auth_expired_handler)
    app.add_exception_handler(ConnectorRateLimitError, connector_rate_limit_handler)
    app.add_exception_handler(ConnectorServiceUnavailableError, connector_unavailable_handler)
    app.add_exception_handler(ReadOnlyConnectorError, connector_read_only_handler)
    app.add_exception_handler(CredentialNotFoundError, credential_not_found_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
