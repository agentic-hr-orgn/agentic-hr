"""
Durable workflow definitions and activities for Agentic HR Core.
"""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        TenantEmailRequest,
        TenantExpiryScanRequest,
        execute_dummy_approved_action_activity,
        execute_dummy_rejected_action_activity,
        get_approval_decision_activity,
        greeting_activity,
        list_active_tenant_ids_activity,
        notify_approval_decision_activity,
        notify_changes_requested_activity,
        notify_resubmitted_activity,
        scan_tenant_expiring_documents_activity,
        schedule_interview_activity,
        send_tenant_email_activity,
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
from app.workflows.document_expiry_workflow import (
    SCAN_RETRY_POLICY,
    DocumentExpiryScanInput,
    DocumentExpiryScanWorkflow,
)
from app.workflows.sample import SampleOnboardingWorkflow

__all__ = [
    "SCAN_RETRY_POLICY",
    "VERIFY_RETRY_POLICY",
    "ApprovalOutcome",
    "ApprovalWorkflowInput",
    "DocumentExpiryScanInput",
    "DocumentExpiryScanWorkflow",
    "DummyApprovalWorkflow",
    "DummyWorkflowInput",
    "HumanApprovalWorkflow",
    "SampleOnboardingWorkflow",
    "TenantEmailRequest",
    "TenantExpiryScanRequest",
    "WaitForApprovalWorkflow",
    "execute_dummy_approved_action_activity",
    "execute_dummy_rejected_action_activity",
    "get_approval_decision_activity",
    "greeting_activity",
    "list_active_tenant_ids_activity",
    "notify_approval_decision_activity",
    "notify_changes_requested_activity",
    "notify_resubmitted_activity",
    "scan_tenant_expiring_documents_activity",
    "schedule_interview_activity",
    "send_tenant_email_activity",
    "send_welcome_notification",
    "verify_approval_record_activity",
    "wait_for_approval",
]
