"""
Health check and error response verification tests using HTTPX.
Verifies GET /health, unique request ID generation, and unified JSON error handling.
"""

import httpx
import pytest

from app.core.config import settings


@pytest.mark.asyncio
async def test_get_health_httpx(async_client: httpx.AsyncClient) -> None:
    """Verifies that GET /health returns 200, valid health payload, and unique request_id."""
    response = await async_client.get("/health")

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers

    data = response.json()
    assert data["status"] == "healthy"
    assert data["environment"] == settings.ENVIRONMENT
    assert data["environment"] in {"development", "test"}
    assert "database" in data
    assert "connected" in data["database"]
    assert "pgvector_enabled" in data["database"]
    assert "models" in data
    assert data["models"]["strong"] == "gemini-3.8-flash"
    assert data["models"]["cheap"] == "gemini-3.5-flash-lite"
    assert data["models"]["embedding"] == "gemini-embedding-2"

    # Request ID in body must match X-Request-ID response header
    assert data["request_id"] is not None
    assert data["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_request_id_propagation_httpx(async_client: httpx.AsyncClient) -> None:
    """Verifies that a caller-supplied X-Request-ID is preserved and propagated."""
    custom_id = "test-custom-request-id-12345"
    response = await async_client.get("/health", headers={"X-Request-ID": custom_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == custom_id
    assert response.json()["request_id"] == custom_id


@pytest.mark.asyncio
async def test_unified_error_format_httpx(async_client: httpx.AsyncClient) -> None:
    """Verifies that non-existent routes return the unified JSON error response with request_id."""
    response = await async_client.get("/api/v1/nonexistent-endpoint")

    assert response.status_code == 404
    assert "X-Request-ID" in response.headers

    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "NOT_FOUND"
    assert data["error"]["message"] is not None
    assert data["error"]["request_id"] == response.headers["X-Request-ID"]
