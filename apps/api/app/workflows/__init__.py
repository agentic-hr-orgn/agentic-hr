"""
Durable workflow definitions and activities for Agentic HR Core.
"""

from app.workflows.activities import (
    execute_dummy_approved_action_activity,
    execute_dummy_rejected_action_activity,
    get_approval_decision_activity,
    greeting_activity,
    notify_approval_decision_activity,
    notify_changes_requested_activity,
    notify_resubmitted_activity,
    schedule_interview_activity,
    send_welcome_notification,
    verify_approval_record_activity,
)
from app.workflows.approval_workflow import (
    VERIFY_RETRY_POLICY,
    ApprovalOutcome,
    ApprovalWorkflowInput,
    DummyApprovalWorkflow,
    DummyWorkflowInput,
    HumanApprovalWorkflow,
    WaitForApprovalWorkflow,
    wait_for_approval,
)
from app.workflows.sample import SampleOnboardingWorkflow

__all__ = [
    "VERIFY_RETRY_POLICY",
    "ApprovalOutcome",
    "ApprovalWorkflowInput",
    "DummyApprovalWorkflow",
    "DummyWorkflowInput",
    "HumanApprovalWorkflow",
    "SampleOnboardingWorkflow",
    "WaitForApprovalWorkflow",
    "execute_dummy_approved_action_activity",
    "execute_dummy_rejected_action_activity",
    "get_approval_decision_activity",
    "greeting_activity",
    "notify_approval_decision_activity",
    "notify_changes_requested_activity",
    "notify_resubmitted_activity",
    "schedule_interview_activity",
    "send_welcome_notification",
    "verify_approval_record_activity",
    "wait_for_approval",
]
