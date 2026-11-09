"""
Temporal Workflow for Durable Human Approval Governance.
Supports multi-round revision cycles, three-decision state transitions (Approve, Request Changes, Reject),
signal deduplication, in-flight worker crash resilience, and audit integration.
Complies with Stage 2 Phase 6 and Phase 7 specifications.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.core.temporal import ApprovalDecisionSignal
    from app.workflows.activities import (
        execute_dummy_approved_action_activity,
        execute_dummy_rejected_action_activity,
        notify_approval_decision_activity,
        notify_changes_requested_activity,
        notify_resubmitted_activity,
        verify_approval_record_activity,
    )


# ==============================================================================
# Data Structures
# ==============================================================================


@dataclass
class ApprovalOutcome:
    """Structured decision outcome returned by wait_for_approval."""

    status: str  # "approved", "rejected", "changes_requested"
    approval_id: str
    decided_by: str
    decision_reason: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class DummyWorkflowInput:
    """Input parameters for initiating DummyApprovalWorkflow."""

    workflow_id: str
    approval_id: str
    tenant_id: str


@dataclass
class ApprovalWorkflowInput:
    """Input parameters for initiating HumanApprovalWorkflow."""

    approval_id: str
    tenant_id: str
    approval_type: str
    initial_payload: dict[str, Any]
    requester_email: str
    approver_role: str


# ==============================================================================
# Helper & Child Workflow for Human Approval Gate
# ==============================================================================


@workflow.defn(sandboxed=False)
class WaitForApprovalWorkflow:
    """
    Durable child workflow coordinating the wait for an authorized human approval signal.
    Enforces signal deduplication, crash resilience, and database verification.
    """

    def __init__(self) -> None:
        self.decision_signal: ApprovalDecisionSignal | None = None
        self.processed: bool = False

    @workflow.signal(name="approval_decision_signal")
    def receive_decision_signal(self, signal: ApprovalDecisionSignal) -> None:
        """
        Receives human approval decision.
        Enforces idempotency and signal deduplication: only the first received signal is processed.
        """
        if self.processed or self.decision_signal is not None:
            workflow.logger.warning(
                f"Ignoring duplicate approval decision signal for approval {signal.approval_id} (action: {signal.action})"
            )
            return

        self.decision_signal = signal
        workflow.logger.info(
            f"Received approval decision signal '{signal.action}' for approval {signal.approval_id} by {signal.decided_by}"
        )

    @workflow.run
    async def run(self, approval_id: str, tenant_id: str | None = None) -> ApprovalOutcome:
        workflow.logger.info(f"WaitForApprovalWorkflow started for approval {approval_id}")

        while True:
            # 1. Deterministically wait until an authorized decision signal arrives
            await workflow.wait_condition(lambda: self.decision_signal is not None)
            sig = self.decision_signal
            self.decision_signal = None

            try:
                # 2. Execute verification activity against authoritative PostgreSQL database
                # This confirms that the decision was genuinely committed with matching status and decider
                verification_result = await workflow.execute_activity(
                    verify_approval_record_activity,
                    args=[sig.approval_id, sig.action, sig.decided_by, sig.tenant_id or tenant_id],
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                self.processed = True
                return ApprovalOutcome(
                    status=sig.action,
                    approval_id=sig.approval_id,
                    decided_by=sig.decided_by,
                    decision_reason=sig.reason,
                    details=verification_result,
                )
            except Exception as exc:
                workflow.logger.warning(
                    f"SECURITY WARNING: Ignoring unauthorized/unverified signal for child approval {sig.approval_id}: {exc}"
                )


async def wait_for_approval(
    approval_id: str,
    tenant_id: str | None = None,
    timeout: timedelta | None = None,
    wait_fn: Any | None = None,
    get_signal_fn: Any | None = None,
    reset_signal_fn: Any | None = None,
) -> ApprovalOutcome:
    """
    Reusable asynchronous workflow helper that pauses execution until an authorized
    human decision is signaled and verified against the PostgreSQL database.
    Can be invoked directly inside any workflow (passing wait_fn / get_signal_fn / reset_signal_fn)
    or invoked standalone as a child workflow (WaitForApprovalWorkflow).
    Enforces security defense against spoofed signals: unverified decisions are discarded while
    the workflow continues waiting for a genuine authorized decision.
    """
    if wait_fn is not None and get_signal_fn is not None:
        while True:
            # Direct in-workflow execution (no child workflow overhead or race conditions)
            await workflow.wait_condition(wait_fn)
            sig = get_signal_fn()
            if reset_signal_fn:
                reset_signal_fn()
            assert sig is not None

            try:
                # Verify against authoritative PostgreSQL database
                verification_result = await workflow.execute_activity(
                    verify_approval_record_activity,
                    args=[sig.approval_id, sig.action, sig.decided_by, sig.tenant_id or tenant_id],
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                return ApprovalOutcome(
                    status=sig.action,
                    approval_id=sig.approval_id,
                    decided_by=sig.decided_by,
                    decision_reason=sig.reason,
                    details=verification_result,
                )
            except Exception as exc:
                workflow.logger.warning(
                    f"SECURITY WARNING: Ignoring unauthorized/unverified signal for approval {sig.approval_id}: {exc}"
                )
    else:
        # Child workflow execution
        return await workflow.execute_child_workflow(
            WaitForApprovalWorkflow.run,
            args=[approval_id, tenant_id],
            id=f"wait-approval-{approval_id}",
            execution_timeout=timeout or timedelta(days=7),
        )


# ==============================================================================
# Phase 7: Dummy Approval Workflow
# ==============================================================================


@workflow.defn(sandboxed=False)
class DummyApprovalWorkflow:
    """
    Dummy approval workflow demonstrating the complete Stage 2 Phase 7 governance lifecycle.
    1. Pauses while waiting for human decision via wait_for_approval(approval_id).
    2. Handles approval and rejection separately with distinct side-effect activities.
    3. Prevents duplicate execution of side-effect activities.
    4. Exposes queries 'get_status' and 'get_outcome'.
    5. Survives worker stops and restarts without state loss.
    """

    def __init__(self) -> None:
        self.status: str = "WAITING_FOR_APPROVAL"
        self.outcome: dict[str, Any] | None = None
        self.decision_signal: ApprovalDecisionSignal | None = None
        self.side_effects_executed: int = 0

    @workflow.signal(name="approval_decision_signal")
    def receive_signal(self, signal: ApprovalDecisionSignal) -> None:
        """
        Receives approval decision signal directly.
        Enforces idempotency and deduplication: only the first received signal is processed.
        """
        if self.side_effects_executed > 0 or self.decision_signal is not None:
            workflow.logger.warning(
                f"Ignoring duplicate signal on DummyApprovalWorkflow for {signal.approval_id}"
            )
            return
        self.decision_signal = signal
        workflow.logger.info(
            f"DummyApprovalWorkflow received decision signal '{signal.action}' for {signal.approval_id} by {signal.decided_by}"
        )

    @workflow.query(name="get_status")
    def get_status(self) -> str:
        """Returns the current lifecycle status of the dummy workflow."""
        return self.status

    @workflow.query(name="get_outcome")
    def get_outcome(self) -> dict[str, Any] | None:
        """Returns the final outcome of the dummy workflow once completed."""
        return self.outcome

    @workflow.run
    async def run(self, input_data: DummyWorkflowInput) -> dict[str, Any]:
        workflow.logger.info(
            f"DummyApprovalWorkflow started: id={input_data.workflow_id}, approval_id={input_data.approval_id}"
        )
        self.status = "WAITING_FOR_APPROVAL"

        def _reset_signal() -> None:
            self.decision_signal = None

        # 1. Deterministically pause execution waiting for human approval decision
        decision_outcome: ApprovalOutcome = await wait_for_approval(
            approval_id=input_data.approval_id,
            tenant_id=input_data.tenant_id,
            wait_fn=lambda: self.decision_signal is not None,
            get_signal_fn=lambda: self.decision_signal,
            reset_signal_fn=_reset_signal,
        )

        workflow.logger.info(
            f"DummyApprovalWorkflow resumed: approval {input_data.approval_id} received decision '{decision_outcome.status}'"
        )

        # 2. Enforce idempotency: prevent duplicate side-effect activity calls
        if self.side_effects_executed > 0:
            workflow.logger.warning("Side effects already executed; ignoring duplicate execution.")
            return self.outcome or {"status": "COMPLETED", "outcome": decision_outcome.status}

        self.side_effects_executed += 1

        # 3. Handle approval and rejection separately with distinct activities
        if decision_outcome.status == "approved":
            side_effect = await workflow.execute_activity(
                execute_dummy_approved_action_activity,
                args=[input_data.approval_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            self.status = "COMPLETED_APPROVED"
            self.outcome = {
                "status": "COMPLETED",
                "outcome": "APPROVED",
                "approval_id": input_data.approval_id,
                "decided_by": decision_outcome.decided_by,
                "decision_reason": decision_outcome.decision_reason,
                "side_effect": side_effect,
            }
            return self.outcome

        elif decision_outcome.status == "rejected":
            side_effect = await workflow.execute_activity(
                execute_dummy_rejected_action_activity,
                args=[input_data.approval_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            self.status = "COMPLETED_REJECTED"
            self.outcome = {
                "status": "COMPLETED",
                "outcome": "REJECTED",
                "approval_id": input_data.approval_id,
                "decided_by": decision_outcome.decided_by,
                "rejection_reason": decision_outcome.decision_reason,
                "side_effect": side_effect,
            }
            return self.outcome

        else:
            self.status = f"COMPLETED_{decision_outcome.status.upper()}"
            self.outcome = {
                "status": "COMPLETED",
                "outcome": decision_outcome.status.upper(),
                "approval_id": input_data.approval_id,
                "decided_by": decision_outcome.decided_by,
                "details": decision_outcome.details,
            }
            return self.outcome


# ==============================================================================
# Existing Multi-Round HumanApprovalWorkflow
# ==============================================================================


@workflow.defn(sandboxed=False)
class HumanApprovalWorkflow:
    """
    Durable workflow coordinating the human-in-the-loop approval gate.
    Maintains workflow state across crashes, handles approver decisions,
    notifies requesters of change requests, and waits for corrections.
    """

    def __init__(self) -> None:
        self.status: str = "pending"
        self.current_revision: int = 1
        self.latest_payload: dict[str, Any] = {}
        self.latest_decision_reason: str | None = None
        self.decided_by: str | None = None
        self.round_history: list[dict[str, Any]] = []

    @workflow.signal
    def decision_signal(self, action: str, decided_by: str, reason: str | None = None) -> None:
        """
        Receives approver decision.
        Enforces idempotency and signal deduplication: only acts if current status is 'pending'.
        """
        if self.status != "pending":
            workflow.logger.warning(
                f"Ignoring duplicate/unexpected decision signal '{action}'; current status is '{self.status}'"
            )
            return

        if action in ("approved", "rejected", "changes_requested"):
            self.status = action
            self.decided_by = decided_by
            self.latest_decision_reason = reason
            self.round_history.append(
                {
                    "revision": self.current_revision,
                    "action": action,
                    "decided_by": decided_by,
                    "reason": reason,
                    "timestamp": workflow.now().isoformat(),
                }
            )
            workflow.logger.info(
                f"Processed decision '{action}' for revision {self.current_revision} by {decided_by}"
            )

    @workflow.signal
    def resubmit_signal(self, new_payload: dict[str, Any], resubmitted_by: str) -> None:
        """
        Receives requester corrected resubmission.
        Enforces idempotency: only acts if current status is 'changes_requested'.
        """
        if self.status != "changes_requested":
            workflow.logger.warning(
                f"Ignoring duplicate/unexpected resubmit signal; current status is '{self.status}'"
            )
            return

        self.current_revision += 1
        self.latest_payload = new_payload
        self.status = "pending"
        self.latest_decision_reason = None
        self.decided_by = None
        self.round_history.append(
            {
                "revision": self.current_revision,
                "action": "resubmitted",
                "resubmitted_by": resubmitted_by,
                "timestamp": workflow.now().isoformat(),
            }
        )
        workflow.logger.info(
            f"Processed resubmission for revision {self.current_revision} by {resubmitted_by}"
        )

    @workflow.query
    def get_approval_state(self) -> dict[str, Any]:
        """Query returning the live in-memory state of the approval workflow."""
        return {
            "status": self.status,
            "current_revision": self.current_revision,
            "latest_payload": self.latest_payload,
            "latest_decision_reason": self.latest_decision_reason,
            "decided_by": self.decided_by,
            "round_history": self.round_history,
        }

    @workflow.run
    async def run(self, input_data: ApprovalWorkflowInput) -> dict[str, Any]:
        """
        Executes the durable approval loop.
        Survives worker crashes and restarts via Temporal event sourcing.
        """
        self.latest_payload = input_data.initial_payload
        workflow.logger.info(
            f"Started HumanApprovalWorkflow for approval {input_data.approval_id} "
            f"(type: {input_data.approval_type}, rev: {self.current_revision})"
        )

        while True:
            # 1. Wait until approver commits a decision (transitions away from 'pending')
            await workflow.wait_condition(lambda: self.status != "pending")

            if self.status == "approved":
                workflow.logger.info(
                    f"Approval {input_data.approval_id} committed as APPROVED at revision {self.current_revision}"
                )
                await workflow.execute_activity(
                    notify_approval_decision_activity,
                    args=[input_data.approval_id, "approved", self.latest_decision_reason],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return {
                    "status": "APPROVED",
                    "approval_id": input_data.approval_id,
                    "final_revision": self.current_revision,
                    "decided_by": self.decided_by,
                    "payload": self.latest_payload,
                    "round_history": self.round_history,
                }

            elif self.status == "rejected":
                workflow.logger.info(
                    f"Approval {input_data.approval_id} committed as REJECTED at revision {self.current_revision}"
                )
                await workflow.execute_activity(
                    notify_approval_decision_activity,
                    args=[input_data.approval_id, "rejected", self.latest_decision_reason],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return {
                    "status": "REJECTED",
                    "approval_id": input_data.approval_id,
                    "final_revision": self.current_revision,
                    "decided_by": self.decided_by,
                    "rejection_reason": self.latest_decision_reason,
                    "round_history": self.round_history,
                }

            elif self.status == "changes_requested":
                workflow.logger.info(
                    f"Changes requested for approval {input_data.approval_id} at revision {self.current_revision}"
                )
                # Dispatch notification activity to requester
                await workflow.execute_activity(
                    notify_changes_requested_activity,
                    args=[
                        input_data.approval_id,
                        input_data.requester_email,
                        self.latest_decision_reason,
                    ],
                    start_to_close_timeout=timedelta(seconds=10),
                )

                # 2. Wait until requester resubmits (status transitions back to 'pending')
                await workflow.wait_condition(lambda: self.status == "pending")

                # Notify approver that corrected draft is ready for review
                await workflow.execute_activity(
                    notify_resubmitted_activity,
                    args=[input_data.approval_id, self.current_revision, input_data.approver_role],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                # Loop continues back to waiting for approver's review on new revision
