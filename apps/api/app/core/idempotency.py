"""
Database-Backed Idempotency Engine for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and Stage 2 Phase 8 specifications.

Guarantees at-most-once execution per tenant/key using PostgreSQL unique constraints
and row-level locking. Caches original HTTP response status and body for deterministic replay.
"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyRecord

logger = logging.getLogger("agentic_hr.idempotency")


class IdempotencyError(Exception):
    """Base exception for idempotency violations."""


class IdempotencyPayloadMismatchError(IdempotencyError):
    """Raised when an existing idempotency key is reused with a differing payload or operation."""


class IdempotencyConflictError(IdempotencyError):
    """Raised when a concurrent operation is currently executing under the same key."""


def compute_payload_hash(payload: Any) -> str:
    """
    Computes a deterministic SHA-256 hexadecimal hash of any request payload.
    Sorts dictionary keys and formats floats/dates canonically.
    """
    if payload is None:
        canonical_str = ""
    elif isinstance(payload, str):
        canonical_str = payload
    elif hasattr(payload, "model_dump"):
        # Pydantic v2 model
        canonical_str = json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    else:
        canonical_str = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


class IdempotencyGate:
    """
    Asynchronous context manager governing idempotent operation lifecycle.
    Manages state transitions: started -> completed (or failed).
    """

    def __init__(
        self,
        session: AsyncSession,
        tenant_id: UUID | str,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
        lock_timeout_seconds: int = 120,
    ) -> None:
        self.session = session
        self.tenant_id = UUID(str(tenant_id))
        self.operation = operation
        self.key = str(idempotency_key).strip()
        self.payload_hash = payload_hash
        self.lock_timeout_seconds = lock_timeout_seconds

        self.record: IdempotencyRecord | None = None
        self.is_replay: bool = False
        self.response_code: int = 200
        self.response_body: dict[str, Any] = {}
        self._completed: bool = False

    async def __aenter__(self) -> Self:
        if not self.key:
            raise ValueError("idempotency_key must be a non-empty string.")

        # 1. Query existing idempotency record with row-level concurrency lock
        stmt = (
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.tenant_id == self.tenant_id,
                IdempotencyRecord.key == self.key,
            )
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            self.record = existing

            # Requirement 3: Reusing a key with a different payload must be rejected
            if existing.payload_hash != self.payload_hash or existing.operation != self.operation:
                logger.warning(
                    f"Idempotency key mismatch: key='{self.key}' tenant='{self.tenant_id}'. "
                    f"Expected hash={existing.payload_hash[:8]}, got={self.payload_hash[:8]}"
                )
                raise IdempotencyPayloadMismatchError(
                    f"Idempotency key '{self.key}' was previously used with a different request payload or operation."
                )

            # Requirement 5: Safely return the original result if already completed
            if existing.status == "completed":
                self.is_replay = True
                self.response_code = existing.response_code or 200
                self.response_body = existing.response_body or {}
                logger.info(
                    f"Idempotency hit (replay): key='{self.key}' tenant='{self.tenant_id}' operation='{self.operation}'"
                )
                return self

            # In-flight concurrent request check
            if existing.status == "started":
                # Check for stale lock timeout
                now = datetime.now(UTC)
                age = (now - existing.locked_at).total_seconds()
                if age < self.lock_timeout_seconds:
                    logger.warning(
                        f"Concurrent request conflict: key='{self.key}' tenant='{self.tenant_id}' (age: {age:.1f}s)"
                    )
                    raise IdempotencyConflictError(
                        f"An operation with idempotency key '{self.key}' is currently in progress. Please retry shortly."
                    )
                else:
                    logger.warning(
                        f"Reclaiming stale idempotency lock for key='{self.key}' (age: {age:.1f}s)"
                    )
                    existing.locked_at = now
                    return self

            # If previous attempt failed, allow retry
            if existing.status == "failed":
                logger.info(f"Retrying previously failed idempotency operation: key='{self.key}'")
                existing.status = "started"
                existing.locked_at = datetime.now(UTC)
                return self

        # 2. No record exists: Insert new 'started' record
        new_record = IdempotencyRecord(
            tenant_id=self.tenant_id,
            key=self.key,
            operation=self.operation,
            payload_hash=self.payload_hash,
            status="started",
            locked_at=datetime.now(UTC),
        )
        self.session.add(new_record)
        try:
            await self.session.flush()
            self.record = new_record
            self.is_replay = False
            return self
        except IntegrityError as exc:
            # Race condition: Another concurrent worker inserted just before us
            await self.session.rollback()
            raise IdempotencyConflictError(
                f"Concurrent creation conflict on idempotency key '{self.key}'."
            ) from exc

    def set_result(self, response_code: int, response_body: dict[str, Any]) -> None:
        """Stores the deterministic response to be committed with the business transaction."""
        self.response_code = response_code
        self.response_body = response_body
        self._completed = True
        if self.record and not self.is_replay:
            self.record.status = "completed"
            self.record.response_code = response_code
            self.record.response_body = response_body

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None and not self.is_replay and self.record:
            # Operation failed: Mark record as 'failed' so caller can retry later
            self.record.status = "failed"
            try:
                await self.session.flush()
            except Exception as flush_err:
                logger.debug(f"Failed to record failure status on key {self.key}: {flush_err}")


async def execute_idempotent_operation(
    session: AsyncSession,
    tenant_id: UUID | str,
    operation: str,
    idempotency_key: str,
    payload_hash: str | None = None,
    payload: Any = None,
    execute_fn: Callable[[], Awaitable[tuple[int, dict[str, Any]]]] | None = None,
) -> tuple[int, dict[str, Any], bool]:
    """
    Functional helper executing a business operation under strict database idempotency.
    Returns: (status_code, response_body, is_replay)
    Raises:
        IdempotencyPayloadMismatchError (422) if key reused with differing payload.
        IdempotencyConflictError (409) if concurrent request is in-flight.
    """
    if payload_hash is None:
        payload_hash = compute_payload_hash(payload)

    async with IdempotencyGate(
        session=session,
        tenant_id=tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
    ) as gate:
        if gate.is_replay:
            return gate.response_code, gate.response_body, True

        if execute_fn is None:
            raise ValueError("execute_fn must be provided when operation is not a replay.")

        code, body = await execute_fn()
        gate.set_result(response_code=code, response_body=body)
        return code, body, False
