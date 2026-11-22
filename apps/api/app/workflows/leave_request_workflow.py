"""
Durable Temporal LeaveRequestWorkflow for Agentic HR Core (Stage 6 Prompt 3).

Strictly sandboxed (`with workflow.unsafe.imports_passed_through():`) with configurable
timer durations (`reminder_hours=24.0`, `escalation_hours=48.0`, `expiry_hours=72.0`)
for fast deterministic testing.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        LeaveDecisionActivityRequest,
        LeaveWorkflowActivityRequest,
        approve_leave_request_activity,
        escalate_leave_request_activity,
        expire_leave_request_activity,
        notify_leave_requested_activity,
        release_leave_reservation_activity,
        send_leave_reminder_activity,
    )

LEAVE_ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
)


@dataclass
class LeaveRequestWorkflowInput:
    """Workflow input with configurable reminder/escalation/expiry timers."""

    tenant_id: str
    leave_request_id: str
    requires_hr_exception: bool = False
    reminder_hours: float = 24.0
    escalation_hours: float = 48.0
    expiry_hours: float = 72.0


@workflow.defn
class LeaveRequestWorkflow:
    """
    Durable multi-day leave request lifecycle workflow:
    1. Notify manager via Gmail + in-app (`notify_leave_requested_activity`).
    2. Wait up to `reminder_hours` -> send 24h reminder (`send_leave_reminder_activity`).
    3. Wait up to `escalation_hours - reminder_hours` -> escalate to backup approver
       (`escalate_leave_request_activity`).
    4. Wait up to `expiry_hours - escalation_hours` -> expire & release reservation
       (`expire_leave_request_activity`).
    5. On approval (plus HR exception approved when `requires_hr_exception=True`) ->
       execute single idempotent `approve_leave_request_activity`.
    6. On rejection or cancellation -> execute idempotent `release_leave_reservation_activity`.
    """

    def __init__(self) -> None:
        self._manager_decision: str | None = None
        self._manager_actor: str | None = None
        self._manager_note: str | None = None
        self._hr_decision: str | None = None
        self._hr_actor: str | None = None
        self._hr_note: str | None = None
        self._cancelled: bool = False
        self._cancel_actor: str | None = None
        self._cancel_reason: str | None = None

    @workflow.signal
    def manager_decision(
        self,
        decision: str,
        decided_by: str,
        note: str | None = None,
    ) -> None:
        self._manager_decision = decision.strip().lower()
        self._manager_actor = decided_by
        self._manager_note = note

    @workflow.signal
    def hr_exception_decision(
        self,
        decision: str,
        decided_by: str,
        note: str | None = None,
    ) -> None:
        self._hr_decision = decision.strip().lower()
        self._hr_actor = decided_by
        self._hr_note = note

    @workflow.signal
    def employee_cancel(self, actor_id: str, reason: str | None = None) -> None:
        self._cancelled = True
        self._cancel_actor = actor_id
        self._cancel_reason = reason

    def _is_resolved(self, requires_hr_exception: bool) -> bool:
        if self._cancelled:
            return True
        if self._manager_decision == "rejected":
            return True
        if self._hr_decision == "rejected":
            return True
        if self._manager_decision == "approved":
            return (not requires_hr_exception) or (self._hr_decision == "approved")
        return False

    @workflow.run
    async def run(self, inp: LeaveRequestWorkflowInput) -> dict[str, Any]:
        req_base = LeaveWorkflowActivityRequest(
            tenant_id=inp.tenant_id,
            leave_request_id=inp.leave_request_id,
        )

        # 1. Notify assigned manager immediately
        await workflow.execute_activity(
            notify_leave_requested_activity,
            req_base,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=LEAVE_ACTIVITY_RETRY_POLICY,
        )

        # 2. Wait up to reminder_hours (24h default)
        if not self._is_resolved(inp.requires_hr_exception):
            try:
                await workflow.wait_condition(
                    lambda: self._is_resolved(inp.requires_hr_exception),
                    timeout=timedelta(hours=max(inp.reminder_hours, 0.0001)),
                )
            except TimeoutError:
                if not self._is_resolved(inp.requires_hr_exception):
                    await workflow.execute_activity(
                        send_leave_reminder_activity,
                        req_base,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=LEAVE_ACTIVITY_RETRY_POLICY,
                    )

        # 3. Wait up to escalation_hours (48h default)
        if not self._is_resolved(inp.requires_hr_exception):
            esc_delta = max(inp.escalation_hours - inp.reminder_hours, 0.0001)
            try:
                await workflow.wait_condition(
                    lambda: self._is_resolved(inp.requires_hr_exception),
                    timeout=timedelta(hours=esc_delta),
                )
            except TimeoutError:
                if not self._is_resolved(inp.requires_hr_exception):
                    await workflow.execute_activity(
                        escalate_leave_request_activity,
                        req_base,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=LEAVE_ACTIVITY_RETRY_POLICY,
                    )

        # 4. Wait up to expiry_hours (72h default)
        if not self._is_resolved(inp.requires_hr_exception):
            exp_delta = max(inp.expiry_hours - inp.escalation_hours, 0.0001)
            try:
                await workflow.wait_condition(
                    lambda: self._is_resolved(inp.requires_hr_exception),
                    timeout=timedelta(hours=exp_delta),
                )
            except TimeoutError:
                if not self._is_resolved(inp.requires_hr_exception):
                    return await workflow.execute_activity(
                        expire_leave_request_activity,
                        req_base,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=LEAVE_ACTIVITY_RETRY_POLICY,
                    )

        # 5. Execute terminal idempotent activity
        if self._cancelled:
            return await workflow.execute_activity(
                release_leave_reservation_activity,
                LeaveDecisionActivityRequest(
                    tenant_id=inp.tenant_id,
                    leave_request_id=inp.leave_request_id,
                    target_status="cancelled",
                    actor_id=self._cancel_actor,
                    note=self._cancel_reason,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=LEAVE_ACTIVITY_RETRY_POLICY,
            )

        if self._manager_decision == "rejected" or self._hr_decision == "rejected":
            actor = self._hr_actor if self._hr_decision == "rejected" else self._manager_actor
            note = self._hr_note if self._hr_decision == "rejected" else self._manager_note
            return await workflow.execute_activity(
                release_leave_reservation_activity,
                LeaveDecisionActivityRequest(
                    tenant_id=inp.tenant_id,
                    leave_request_id=inp.leave_request_id,
                    target_status="rejected",
                    actor_id=actor,
                    note=note,
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=LEAVE_ACTIVITY_RETRY_POLICY,
            )

        return await workflow.execute_activity(
            approve_leave_request_activity,
            LeaveDecisionActivityRequest(
                tenant_id=inp.tenant_id,
                leave_request_id=inp.leave_request_id,
                target_status="approved",
                actor_id=self._manager_actor or self._hr_actor,
                note=self._manager_note or self._hr_note,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=LEAVE_ACTIVITY_RETRY_POLICY,
        )
