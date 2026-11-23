"""
Durable Temporal TicketSLAWorkflow for Agentic HR Core (Stage 7 Prompt 2).

Strictly sandboxed (`with workflow.unsafe.imports_passed_through():`) with configurable
timer durations (`reminder_delay_seconds=72000.0`, `breach_delay_seconds=86400.0`) so both
production 20h/24h SLAs and deterministic time-skipping tests execute cleanly.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        TicketSLAActivityRequest,
        escalate_ticket_sla_breach_activity,
        send_ticket_sla_reminder_activity,
    )

TICKET_SLA_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
)


@dataclass
class TicketSLAWorkflowInput:
    """Workflow input with configurable reminder & breach timers (in seconds)."""

    tenant_id: str
    ticket_id: str
    reminder_delay_seconds: float = 72000.0
    breach_delay_seconds: float = 86400.0


@workflow.defn
class TicketSLAWorkflow:
    """
    Durable Helpdesk Ticket SLA Workflow:
    1. Wait up to `reminder_delay_seconds` or `ticket_resolved` signal ->
       if still unresolved, run `send_ticket_sla_reminder_activity`.
    2. Wait up to `breach_delay_seconds - reminder_delay_seconds` or `ticket_resolved` signal ->
       if still unresolved, run `escalate_ticket_sla_breach_activity`.
    3. Exit cleanly on resolution/closure signal.
    """

    def __init__(self) -> None:
        self._resolved: bool = False
        self._resolved_by: str | None = None
        self._resolved_status: str | None = None

    @workflow.signal
    def ticket_resolved(
        self,
        resolved_by: str | None = None,
        status: str = "resolved",
    ) -> None:
        self._resolved = True
        self._resolved_by = resolved_by
        self._resolved_status = status

    @workflow.run
    async def run(self, inp: TicketSLAWorkflowInput) -> dict[str, Any]:
        req = TicketSLAActivityRequest(
            tenant_id=inp.tenant_id,
            ticket_id=inp.ticket_id,
        )

        # 1. Wait until pre-breach reminder deadline or ticket resolution
        rem_secs = max(float(inp.reminder_delay_seconds), 0.01)
        if not self._resolved:
            try:
                await workflow.wait_condition(
                    lambda: self._resolved,
                    timeout=timedelta(seconds=rem_secs),
                )
            except TimeoutError:
                if not self._resolved:
                    await workflow.execute_activity(
                        send_ticket_sla_reminder_activity,
                        req,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=TICKET_SLA_RETRY_POLICY,
                    )

        # 2. Wait until SLA breach deadline or ticket resolution
        if not self._resolved:
            remaining_secs = max(float(inp.breach_delay_seconds) - rem_secs, 0.01)
            try:
                await workflow.wait_condition(
                    lambda: self._resolved,
                    timeout=timedelta(seconds=remaining_secs),
                )
            except TimeoutError:
                if not self._resolved:
                    return await workflow.execute_activity(
                        escalate_ticket_sla_breach_activity,
                        req,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=TICKET_SLA_RETRY_POLICY,
                    )

        return {
            "status": self._resolved_status or "resolved",
            "ticket_id": inp.ticket_id,
            "resolved_by": self._resolved_by,
        }
