"""
Automated Tests for HumanApprovalWorkflow Orchestration.
Verifies multi-round revision coordination, signal deduplication,
Temporal worker resilience, and activity integration using time-skipping test environments.
Complies with Stage 2 specifications.
"""

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.core.temporal import ApprovalDecisionSignal
from app.workflows import (
    ApprovalWorkflowInput,
    DummyApprovalWorkflow,
    DummyWorkflowInput,
    HumanApprovalWorkflow,
    WaitForApprovalWorkflow,
    execute_dummy_approved_action_activity,
    execute_dummy_rejected_action_activity,
    notify_approval_decision_activity,
    notify_changes_requested_activity,
    notify_resubmitted_activity,
)


@pytest.mark.asyncio
async def test_approval_workflow_normal_approval() -> None:
    """Verifies workflow executes to APPROVED when receiving decision_signal('approved')."""
    env = await WorkflowEnvironment.start_time_skipping()
    task_queue = "test-approval-queue-01"

    async with (
        env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=[HumanApprovalWorkflow],
            activities=[
                notify_approval_decision_activity,
                notify_changes_requested_activity,
                notify_resubmitted_activity,
            ],
        ),
    ):
        input_data = ApprovalWorkflowInput(
            approval_id="appr-test-101",
            tenant_id="tenant-test-01",
            approval_type="leave_request",
            initial_payload={"days_count": 2.0, "reason": "Conference"},
            requester_email="agentic.hr.test.ubaid.employee@gmail.com",
            approver_role="manager",
        )

        handle = await env.client.start_workflow(
            HumanApprovalWorkflow.run,
            input_data,
            id="workflow-appr-test-101",
            task_queue=task_queue,
        )

        # Send approval signal
        await handle.signal(
            HumanApprovalWorkflow.decision_signal,
            args=["approved", "user-manager-id", "Approved by team lead."],
        )

        result = await handle.result()
        assert result["status"] == "APPROVED"
        assert result["final_revision"] == 1
        assert result["decided_by"] == "user-manager-id"


@pytest.mark.asyncio
async def test_approval_workflow_multi_round_corrections() -> None:
    """
    Verifies multi-round correction cycle:
    Round 1: changes_requested -> resubmit_signal -> Round 2: approved.
    """
    env = await WorkflowEnvironment.start_time_skipping()
    task_queue = "test-approval-queue-02"

    async with (
        env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=[HumanApprovalWorkflow],
            activities=[
                notify_approval_decision_activity,
                notify_changes_requested_activity,
                notify_resubmitted_activity,
            ],
        ),
    ):
        input_data = ApprovalWorkflowInput(
            approval_id="appr-test-102",
            tenant_id="tenant-test-01",
            approval_type="leave_request",
            initial_payload={"days_count": 5.0, "reason": "Round 1 draft"},
            requester_email="agentic.hr.test.ubaid.employee@gmail.com",
            approver_role="manager",
        )

        handle = await env.client.start_workflow(
            HumanApprovalWorkflow.run,
            input_data,
            id="workflow-appr-test-102",
            task_queue=task_queue,
        )

        # 1. Approver requests changes
        await handle.signal(
            HumanApprovalWorkflow.decision_signal,
            args=["changes_requested", "user-manager-id", "Reduce duration to 3 days."],
        )

        # Query state to verify changes_requested
        state1 = await handle.query(HumanApprovalWorkflow.get_approval_state)
        assert state1["status"] == "changes_requested"
        assert state1["current_revision"] == 1

        # 2. Requester sends resubmission signal
        await handle.signal(
            HumanApprovalWorkflow.resubmit_signal,
            args=[{"days_count": 3.0, "reason": "Amended to 3 days"}, "user-employee-id"],
        )

        # Query state to verify status is back to pending and rev is 2
        state2 = await handle.query(HumanApprovalWorkflow.get_approval_state)
        assert state2["status"] == "pending"
        assert state2["current_revision"] == 2

        # 3. Approver approves Round 2
        await handle.signal(
            HumanApprovalWorkflow.decision_signal,
            args=["approved", "user-manager-id", "Approved."],
        )

        result = await handle.result()
        assert result["status"] == "APPROVED"
        assert result["final_revision"] == 2
        assert len(result["round_history"]) >= 3


@pytest.mark.asyncio
async def test_approval_workflow_signal_deduplication() -> None:
    """Verifies duplicate signals do not corrupt workflow state."""
    env = await WorkflowEnvironment.start_time_skipping()
    task_queue = "test-approval-queue-03"

    async with (
        env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=[HumanApprovalWorkflow],
            activities=[
                notify_approval_decision_activity,
                notify_changes_requested_activity,
                notify_resubmitted_activity,
            ],
        ),
    ):
        input_data = ApprovalWorkflowInput(
            approval_id="appr-test-103",
            tenant_id="tenant-test-01",
            approval_type="leave_request",
            initial_payload={"days_count": 1.0, "reason": "Dedup test"},
            requester_email="agentic.hr.test.ubaid.employee@gmail.com",
            approver_role="manager",
        )

        handle = await env.client.start_workflow(
            HumanApprovalWorkflow.run,
            input_data,
            id="workflow-appr-test-103",
            task_queue=task_queue,
        )

        # Premature resubmit signal when status is pending should be safely ignored
        await handle.signal(
            HumanApprovalWorkflow.resubmit_signal,
            args=[{"days_count": 1.0}, "user-employee-id"],
        )
        state = await handle.query(HumanApprovalWorkflow.get_approval_state)
        assert state["status"] == "pending"
        assert state["current_revision"] == 1

        # Approve and send duplicate approval signal
        await handle.signal(
            HumanApprovalWorkflow.decision_signal,
            args=["approved", "user-manager-id", "First approval."],
        )
        await handle.signal(
            HumanApprovalWorkflow.decision_signal,
            args=["approved", "user-manager-id", "Duplicate approval."],
        )

        result = await handle.result()
        assert result["status"] == "APPROVED"
        assert result["final_revision"] == 1


@pytest.mark.asyncio
async def test_dummy_approval_workflow_queued_signals_and_transient_retry() -> None:
    """
    Verifies H1 & M1 fixes on DummyApprovalWorkflow:
    1. When an unverified/spoofed signal is immediately followed by a valid signal,
       the valid signal is queued (not dropped) and processed after the unverified signal fails.
    2. When verify_approval_record_activity encounters a transient database error,
       VERIFY_RETRY_POLICY retries the verification rather than discarding the valid signal.
    """
    env = await WorkflowEnvironment.start_time_skipping()
    task_queue = "test-approval-queue-04"
    call_counts = {"attempts": 0}

    @activity.defn(name="verify_approval_record_activity")
    async def mock_verify_approval_record_activity(
        approval_id: str,
        expected_decision: str,
        decided_by: str,
        tenant_id: str | None = None,
    ) -> dict[str, object]:
        # Spoofed decider fails with non_retryable=True
        if decided_by == "attacker-user-id":
            raise ApplicationError("Spoofed decider rejected", non_retryable=True)

        # Valid decider: simulate 1 transient DB error, then succeed on retry 2
        call_counts["attempts"] += 1
        if call_counts["attempts"] == 1:
            raise RuntimeError("Simulated transient PostgreSQL connection reset")

        return {
            "verified": True,
            "approval_id": approval_id,
            "status": expected_decision,
            "decided_by": decided_by,
            "tenant_id": tenant_id,
        }

    async with (
        env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=[DummyApprovalWorkflow, WaitForApprovalWorkflow],
            activities=[
                mock_verify_approval_record_activity,
                execute_dummy_approved_action_activity,
                execute_dummy_rejected_action_activity,
            ],
        ),
    ):
        handle = await env.client.start_workflow(
            DummyApprovalWorkflow.run,
            DummyWorkflowInput(
                workflow_id="wf-queued-retry-test",
                approval_id="appr-queued-104",
                tenant_id="tenant-trusted-01",
            ),
            id="wf-queued-retry-test",
            task_queue=task_queue,
        )

        # Send spoofed signal AND legitimate signal back-to-back before verification finishes
        await handle.signal(
            "approval_decision_signal",
            ApprovalDecisionSignal(
                approval_id="appr-queued-104",
                action="approved",
                decided_by="attacker-user-id",
                tenant_id="tenant-spoofed-99",
                reason="Spoofed signal",
            ),
        )
        await handle.signal(
            "approval_decision_signal",
            ApprovalDecisionSignal(
                approval_id="appr-queued-104",
                action="approved",
                decided_by="legit-owner-id",
                tenant_id="tenant-spoofed-99",
                reason="Legitimate approval after transient DB glitch",
            ),
        )

        result = await handle.result()
        assert result["status"] == "COMPLETED"
        assert result["outcome"] == "APPROVED"
        assert result["decided_by"] == "legit-owner-id"
        # Confirm transient retry occurred on legitimate signal (2 attempts)
        assert call_counts["attempts"] == 2
