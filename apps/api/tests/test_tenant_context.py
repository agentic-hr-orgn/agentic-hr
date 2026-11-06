"""
Tests for PostgreSQL Tenant Context and Row-Level Security isolation primitives.
Verifies set_config(..., true) parameterization and transaction scoping.
"""

import pytest

from app.db.session import AsyncSessionLocal
from app.models.tenant import get_tenant_context, set_tenant_context


@pytest.mark.asyncio
async def test_set_and_get_tenant_context() -> None:
    """Verifies that set_tenant_context sets app.current_tenant_id within a transaction."""
    test_tenant = "tenant-acme-test-999"

    async with AsyncSessionLocal() as session, session.begin():
        # Initially, tenant context should be empty/null
        initial_context = await get_tenant_context(session)
        assert initial_context in ("", None)

        # Set the tenant context using set_config(..., true)
        await set_tenant_context(session, test_tenant)

        # Verify it matches the test tenant within this transaction
        active_context = await get_tenant_context(session)
        assert active_context == test_tenant


@pytest.mark.asyncio
async def test_set_tenant_context_validation() -> None:
    """Verifies that empty tenant_id values are rejected."""
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError, match="tenant_id must be a valid non-empty string"):
            await set_tenant_context(session, "")

        with pytest.raises(ValueError, match="tenant_id must be a valid non-empty string"):
            await set_tenant_context(session, "   ")
