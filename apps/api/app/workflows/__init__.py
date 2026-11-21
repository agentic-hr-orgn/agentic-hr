"""
Durable workflow definitions and activities for Agentic HR Core.
"""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        PolicyIngestionFailRequest,
        PolicyIngestionStepRequest,
        TenantEmailRequest,
        TenantExpiryScanRequest,
        chunk_policy_document_activity,
        embed_policy_chunks_activity,
        execute_dummy_approved_action_activity,
        execute_dummy_rejected_action_activity,
        finalize_policy_ingestion_activity,
        get_approval_decision_activity,
        greeting_activity,
        list_active_tenant_ids_activity,
        mark_policy_ingestion_failed_activity,
        notify_approval_decision_activity,
        notify_changes_requested_activity,
        notify_resubmitted_activity,
        parse_policy_document_activity,
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
from app.workflows.policy_ingestion_workflow import (
    INGESTION_RETRY_POLICY,
    PolicyIngestionWorkflow,
)
from app.workflows.sample import SampleOnboardingWorkflow

__all__ = [
    "INGESTION_RETRY_POLICY",
    "SCAN_RETRY_POLICY",
    "VERIFY_RETRY_POLICY",
    "ApprovalOutcome",
    "ApprovalWorkflowInput",
    "DocumentExpiryScanInput",
    "DocumentExpiryScanWorkflow",
    "DummyApprovalWorkflow",
    "DummyWorkflowInput",
    "HumanApprovalWorkflow",
    "PolicyIngestionFailRequest",
    "PolicyIngestionStepRequest",
    "PolicyIngestionWorkflow",
    "SampleOnboardingWorkflow",
    "TenantEmailRequest",
    "TenantExpiryScanRequest",
    "WaitForApprovalWorkflow",
    "chunk_policy_document_activity",
    "embed_policy_chunks_activity",
    "execute_dummy_approved_action_activity",
    "execute_dummy_rejected_action_activity",
    "finalize_policy_ingestion_activity",
    "get_approval_decision_activity",
    "greeting_activity",
    "list_active_tenant_ids_activity",
    "mark_policy_ingestion_failed_activity",
    "notify_approval_decision_activity",
    "notify_changes_requested_activity",
    "notify_resubmitted_activity",
    "parse_policy_document_activity",
    "scan_tenant_expiring_documents_activity",
    "schedule_interview_activity",
    "send_tenant_email_activity",
    "send_welcome_notification",
    "verify_approval_record_activity",
    "wait_for_approval",
]
