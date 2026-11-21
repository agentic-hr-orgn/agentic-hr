"""
Pytest Fixtures for Agentic HR Core Backend Testing.
Provides HTTPX AsyncClient and FastAPI TestClient instances.
"""

import os
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

os.environ.setdefault("INGEST_INLINE", "true")

from app.main import app
from tests.isolation_harness import MultiTenantTestContext, load_multi_tenant_context


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


@pytest_asyncio.fixture
async def isolation_context() -> MultiTenantTestContext:
    """Fixture providing cached multi-tenant test context for Company A & B."""
    from app.core.clerk import register_test_signing_key
    from tests.isolation_harness import TEST_ISOLATION_KID, _public_key

    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    return await load_multi_tenant_context()


@pytest_asyncio.fixture(autouse=True)
async def cleanup_engine_connections() -> AsyncGenerator[None, None]:
    """Ensures pooled connections are cleaned up after each test."""
    yield
    from app.db.session import engine

    await engine.dispose()
