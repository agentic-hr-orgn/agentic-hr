"""
Pytest Fixtures for Agentic HR Core Backend Testing.
Provides HTTPX AsyncClient and FastAPI TestClient instances.
"""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.main import app


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTPX client for non-blocking endpoint testing."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def client() -> TestClient:
    """Synchronous test client for rapid local testing."""
    return TestClient(app)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_engine_connections() -> AsyncGenerator[None, None]:
    """Ensures pooled connections are cleaned up after each test."""
    yield
    from app.db.session import engine

    await engine.dispose()
