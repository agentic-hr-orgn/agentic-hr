"""
Authoritative Common Connector Call Wrapper for Agentic HR Core.
Complies with:
- ADR 002 (Multi-Tenancy & Per-Tenant Isolation)
- ADR 005 (Postgres is Authoritative)
- ADR 006 (Google Workspace Workaround & Least Privilege)

Enforces:
1. Client-Side Read-Only Mode (blocks write operations with ReadOnlyConnectorError / HTTP 403)
2. Circuit-Breaker State (blocks calls when needs_reconnect or jobs_paused)
3. Per-Tenant Sliding-Window Redis Rate Limiting (check_rate_limit)
4. Bounded Exponential Backoff + Jitter on transient errors (429, 503, network timeouts)
5. Automated Circuit Breaker Trip on 401/invalid_grant (handle_auth_failure)
6. Immutable Audit Logging (write_audit on all external write operations)
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import check_rate_limit
from app.models.connector import ConnectorCredential
from app.services.audit_service import write_audit
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorError,
    ConnectorRateLimitError,
    ConnectorService,
    ConnectorServiceUnavailableError,
    ReadOnlyConnectorError,
)

logger = logging.getLogger("agentic_hr.connector_wrapper")

T = TypeVar("T")


async def execute_connector_call(
    session: AsyncSession,
    *,
    tenant_id: UUID | str,
    credential: ConnectorCredential,
    operation: str,
    is_write: bool,
    call_factory: Callable[[str], Awaitable[T]],
    actor_id: UUID | str | None = None,
    rate_limit_scope: str | None = None,
    max_requests_per_minute: int = 60,
    max_retries: int = 3,
    base_backoff_seconds: float = 0.1,
    max_backoff_seconds: float = 5.0,
    audit_target_type: str = "connector_credential",
    audit_target_id: UUID | str | None = None,
    audit_details: dict[str, Any] | None = None,
) -> T:
    """
    Executes a connector operation wrapped in governance guards:
    - Read-Only check
    - Circuit-breaker check
    - Redis rate limiting
    - AES-256-GCM token decryption
    - Exponential backoff with jitter on 429/503
    - Immediate circuit-breaker trip on 401
    - Immutable audit logging on writes
    """
    t_id = UUID(str(tenant_id))

    # 1. Read-Only Switch Check
    if is_write and credential.is_read_only:
        logger.warning(
            f"[ReadOnlyConnector] Blocked write operation '{operation}' "
            f"for credential '{credential.id}' in tenant '{t_id}'."
        )
        raise ReadOnlyConnectorError(
            f"Connector '{credential.id}' is configured in read-only mode. "
            f"Write operation '{operation}' is prohibited."
        )

    # 2. Circuit-Breaker Check
    if credential.status == "needs_reconnect" or credential.jobs_paused:
        logger.warning(
            f"[CircuitBreaker] Connector '{credential.id}' is in status '{credential.status}' "
            f"(jobs_paused={credential.jobs_paused}). Operation '{operation}' blocked."
        )
        raise ConnectorAuthExpiredError(
            f"Connector '{credential.id}' requires reconnect. Automated connector jobs are paused."
        )

    # 3. Per-Tenant Redis Sliding-Window Rate Limit Check
    scope = rate_limit_scope or f"connector.{credential.provider}.{operation}"
    rl = await check_rate_limit(
        tenant_id=str(t_id),
        user_id=str(actor_id) if actor_id else None,
        scope=scope,
        max_requests=max_requests_per_minute,
        window_seconds=60,
    )
    if not rl.allowed:
        logger.warning(
            f"[RateLimit] Tenant '{t_id}' exceeded quota for scope '{scope}'. "
            f"Retry-After: {rl.retry_after}s."
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for connector operation '{operation}'.",
            headers={"Retry-After": str(rl.retry_after)},
        )

    # 4. Decrypt Access Token & Ensure Validity (Auto-Refresh if near or past expiry)
    access_token = await ConnectorService.ensure_valid_access_token(session, credential)

    # 5. Resilient Invocation Loop (Exponential Backoff + Jitter)
    result: T
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = await call_factory(access_token)
            break
        except ConnectorAuthExpiredError as auth_err:
            # Immediate Circuit-Breaker Trip: No retries on 401 / invalid_grant
            logger.error(
                f"[AuthFailure] Provider returned 401 for credential '{credential.id}'. "
                f"Tripping circuit-breaker to needs_reconnect."
            )
            await ConnectorService.handle_auth_failure(
                session,
                tenant_id=t_id,
                credential_id=credential.id,
                error_detail=str(auth_err),
            )
            raise
        except (ConnectorRateLimitError, ConnectorServiceUnavailableError) as transient_err:
            last_error = transient_err
            if attempt < max_retries:
                # Calculate backoff with jitter
                delay = min(
                    base_backoff_seconds * (2**attempt) + random.uniform(0.01, 0.05),
                    max_backoff_seconds,
                )
                if isinstance(transient_err, ConnectorRateLimitError):
                    delay = max(delay, float(transient_err.retry_after))

                logger.warning(
                    f"[TransientRetry] Attempt {attempt + 1}/{max_retries} failed for '{operation}': "
                    f"{transient_err}. Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"[RetriesExhausted] Max retries ({max_retries}) exhausted for '{operation}': {transient_err}"
                )
                raise
        except Exception as unhandled_err:
            # Fatal or unhandled exception -> fail immediately
            logger.error(
                f"[ConnectorCallFailed] Unhandled error during '{operation}': {unhandled_err}"
            )
            raise
    else:
        if last_error:
            raise last_error
        raise ConnectorError(f"Operation '{operation}' failed after {max_retries} retries.")

    # 6. Immutable Audit Logging (Writes Only)
    if is_write:
        details = audit_details or {}
        details.setdefault("provider", credential.provider)
        details.setdefault("account_email", credential.account_email)
        details.setdefault("operation", operation)

        await write_audit(
            session,
            action=f"connector.{operation}",
            target_type=audit_target_type,
            target_id=audit_target_id or credential.id,
            details=details,
            actor_id=actor_id,
            tenant_id=t_id,
        )
        await session.flush()

    return result


async def execute_connector_call_for_tenant(
    session: AsyncSession,
    *,
    tenant_id: UUID | str,
    provider: str = "google",
    account_email: str | None = None,
    operation: str,
    is_write: bool,
    call_factory: Callable[[str], Awaitable[T]],
    actor_id: UUID | str | None = None,
    rate_limit_scope: str | None = None,
    max_requests_per_minute: int = 60,
    max_retries: int = 3,
    base_backoff_seconds: float = 0.1,
    audit_target_type: str = "connector_credential",
    audit_target_id: UUID | str | None = None,
    audit_details: dict[str, Any] | None = None,
) -> T:
    """
    Convenience method that retrieves the active credential for the tenant
    and passes it through the execute_connector_call governance wrapper.
    """
    t_id = UUID(str(tenant_id))
    credential = await ConnectorService.get_active_credential(
        session, tenant_id=t_id, provider=provider, account_email=account_email
    )
    return await execute_connector_call(
        session,
        tenant_id=t_id,
        credential=credential,
        operation=operation,
        is_write=is_write,
        call_factory=call_factory,
        actor_id=actor_id,
        rate_limit_scope=rate_limit_scope,
        max_requests_per_minute=max_requests_per_minute,
        max_retries=max_retries,
        base_backoff_seconds=base_backoff_seconds,
        audit_target_type=audit_target_type,
        audit_target_id=audit_target_id,
        audit_details=audit_details,
    )
