"""
Temporal Client Bridge for Agentic HR Core.
Manages connections to Temporal Server and provides helpers to signal workflows.
Complies with ADR 002, ADR 003, and Stage 2 Phase 7 specifications.
"""

import logging
from dataclasses import dataclass

from temporalio.client import Client

from app.core.config import settings

logger = logging.getLogger("agentic_hr.temporal")

_temporal_client: Client | None = None


@dataclass
class ApprovalDecisionSignal:
    """Structured signal payload dispatched to waiting approval workflows."""

    approval_id: str
    action: str
    decided_by: str
    tenant_id: str | None = None
    reason: str | None = None


def set_temporal_client(client: Client | None) -> None:
    """Sets an explicit Temporal client instance (used for test environment injection)."""
    global _temporal_client
    _temporal_client = client


def reset_temporal_client() -> None:
    """Resets the cached Temporal client (useful for unit tests and environment switches)."""
    global _temporal_client
    _temporal_client = None


async def get_temporal_client() -> Client:
    """
    Returns an async Temporal Client instance, caching the active connection.
    Connects to the configured Temporal server host (settings.TEMPORAL_ADDRESS) if not already set.
    """
    global _temporal_client
    if _temporal_client is None:
        logger.info(f"Connecting to Temporal server at {settings.TEMPORAL_ADDRESS}...")
        _temporal_client = await Client.connect(settings.TEMPORAL_ADDRESS)
        logger.info("Successfully connected to Temporal server.")
    return _temporal_client


async def signal_approval_workflow(
    approval_id: str,
    action: str,
    decided_by: str,
    tenant_id: str | None = None,
    reason: str | None = None,
    workflow_id: str | None = None,
    client: Client | None = None,
) -> bool:
    """
    Signals waiting Temporal approval workflows that an authorized decision has been committed.
    Dispatches 'approval_decision_signal' carrying:
    - approval_id (UUID string)
    - action ('approved', 'rejected', 'changes_requested')
    - decided_by (deciding User UUID string)
    - tenant_id (optional Tenant UUID string for RLS validation)
    - reason (optional explanation / correction instructions)

    Attempts delivery to:
    1. Explicit workflow_id (if specified on approval record)
    2. Convention child workflow ID: f"wait-approval-{approval_id}"
    3. Convention parent workflow ID: f"dummy-approval-{approval_id}"

    Gracefully logs and returns False if the workflow is offline or completed,
    guaranteeing that API requests and database transactions never fail due to signaling.
    """
    signal_payload = ApprovalDecisionSignal(
        approval_id=approval_id,
        action=action,
        decided_by=decided_by,
        tenant_id=tenant_id,
        reason=reason,
    )

    try:
        active_client = client or await get_temporal_client()
    except Exception as exc:
        logger.warning(
            f"Could not connect to Temporal server to signal approval {approval_id}: {exc}"
        )
        return False

    targets = []
    if workflow_id:
        targets.append(workflow_id)
    # Always include parent and child conventional workflow IDs
    parent_id = f"dummy-approval-{approval_id}"
    child_id = f"wait-approval-{approval_id}"
    if parent_id not in targets:
        targets.append(parent_id)
    if child_id not in targets:
        targets.append(child_id)

    delivered = False
    for target in targets:
        try:
            handle = active_client.get_workflow_handle(target)
            await handle.signal("approval_decision_signal", signal_payload)
            logger.info(
                f"Successfully signaled workflow '{target}' with decision '{action}' for approval {approval_id}"
            )
            delivered = True
        except Exception as exc:
            logger.debug(f"Target workflow '{target}' not listening or signal failed: {exc}")

    return delivered
