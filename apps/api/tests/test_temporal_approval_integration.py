"""
Automated Integration Test Suite for Stage 2 Phase 7:
TEMPORAL HUMAN APPROVAL WORKFLOW.

Verifies:
1. Exact Worker Restart Scenario:
   - Start dummy workflow.
   - Request human approval.
   - Confirm workflow is waiting (WAITING_FOR_APPROVAL).
   - Stop Temporal worker.
   - Verify workflow persistence on Temporal Server across worker shutdown.
   - Restart Temporal worker on same task queue.
   - Approve from the application (API).
   - Confirm workflow resumes, replaying history, and completes with outcome=APPROVED.
2. Rejection Scenario:
   - Workflow resumes on rejection, executes rejection cleanup activity, and completes with outcome=REJECTED.
3. Duplicate Signal Prevention:
   - Duplicate signals are discarded; side-effect activities execute strictly once.
4. Unauthorized Decision Defense (Correction 2):
   - Spoofed signals without database confirmation are rejected by verify_approval_record_activity.
   - Workflow logs a security warning and continues waiting for a legitimate authorized decision.
5. Identifiers Only (ADR 003 Privacy Boundary):
   - Workflow arguments and history contain only approval_id, never sensitive payroll/CNIC payloads.
"""
import asyncio
from datetime import timedelta

import httpx
import pytest
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

import app.core.temporal as temporal_core
from app.core.clerk import register_test_signing_key
from app.core.config import settings
from app.core.temporal import ApprovalDecisionSignal
from app.workflows import (
    DummyApprovalWorkflow,
    DummyWorkflowInput,
    WaitForApprovalWorkflow,
    execute_dummy_approved_action_activity,
    execute_dummy_rejected_action_activity,
    verify_approval_record_activity,
)
from tests.isolation_harness import (
    TEST_ISOLATION_KID,
    MultiTenantTestContext,
    _public_key,
)


@pytest.fixture(autouse=True)
def setup_isolation_keys():
    """Ensure test RSA keys are registered for offline token verification."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    yield


async def get_test_environment() -> WorkflowEnvironment:
    """
    Returns a Temporal WorkflowEnvironment.
    Prefers connecting to the active local test server (localhost:7233) for faithful worker-restart testing.
    Falls back to start_time_skipping() if local server is unreachable.
    """
    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        return WorkflowEnvironment.from_client(client)
    except Exception:
        return await WorkflowEnvironment.start_time_skipping()


# ==============================================================================
# 1. Exact Worker Restart Scenario
# ==============================================================================


@pytest.mark.asyncio
async def test_01_exact_worker_restart_scenario(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Mandatory Test Scenario 1:
    - Start dummy workflow.
    - Request human approval via application.
    - Confirm workflow is waiting (WAITING_FOR_APPROVAL).
    - Stop the Temporal worker.
    - Confirm the workflow persists on Temporal Server across worker shutdown.
    - Restart the worker on the same task queue.
    - Approve from the application (POST /api/v1/approvals/{id}/approve).
    - Confirm the workflow resumes successfully, executes approved side effects, and records completion.
    """
    ctx = isolation_context
    headers_employee = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    headers_owner = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Step A: Request human approval via API
    create_payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "annual",
            "start_date": "2026-12-01",
            "end_date": "2026-12-05",
            "days_count": 5.0,
            "reason": "Annual family vacation and rest.",
        },
        "approver_role": "owner",
    }
    create_resp = await async_client.post(
        "/api/v1/approvals",
        headers=headers_employee,
        json=create_payload,
    )
    assert create_resp.status_code == 201, create_resp.text
    approval_data = create_resp.json()
    approval_id = approval_data["id"]

    # Step B: Start Temporal test environment
    env = await get_test_environment()
    temporal_core.set_temporal_client(env.client)
    task_queue = f"tq-worker-restart-{approval_id}"
    workflow_id = f"dummy-approval-{approval_id}"

    shared_workflows = [DummyApprovalWorkflow, WaitForApprovalWorkflow]
    shared_activities = [
        verify_approval_record_activity,
        execute_dummy_approved_action_activity,
        execute_dummy_rejected_action_activity,
    ]

    try:
        async with env:
            # Phase 1: Worker 1 Starts and runs
            worker_1 = Worker(
                env.client,
                task_queue=task_queue,
                workflows=shared_workflows,
                activities=shared_activities,
            )
            async with worker_1:
                # Start dummy workflow with test-only bounded execution timeout (Correction 1)
                handle = await env.client.start_workflow(
                    DummyApprovalWorkflow.run,
                    DummyWorkflowInput(
                        workflow_id=workflow_id,
                        approval_id=approval_id,
                        tenant_id=ctx.company_a.tenant_id,
                    ),
                    id=workflow_id,
                    task_queue=task_queue,
                    execution_timeout=timedelta(seconds=45),
                )

                # Confirm workflow is waiting
                status = await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
                assert status == "WAITING_FOR_APPROVAL"

            # Worker 1 is now completely STOPPED / SHUT DOWN.
            # Confirm workflow persists on server across worker shutdown
            desc = await handle.describe(rpc_timeout=timedelta(seconds=5))
            assert desc.status == WorkflowExecutionStatus.RUNNING

            await asyncio.sleep(0.1)

            # Phase 2: Worker 2 restarts on same task queue
            worker_2 = Worker(
                env.client,
                task_queue=task_queue,
                workflows=shared_workflows,
                activities=shared_activities,
            )
            async with worker_2:
                # Phase 3: Approve from the application (FastAPI route)
                approve_resp = await async_client.post(
                    f"/api/v1/approvals/{approval_id}/approve",
                    headers=headers_owner,
                    json={"reason": "Approved after comprehensive review by owner."},
                )
                assert approve_resp.status_code == 200, approve_resp.text
                assert approve_resp.json()["status"] == "approved"

                # Phase 4: Confirm workflow unblocks on restarted worker, executes side effects, and completes
                result = await handle.result()
                assert result["status"] == "COMPLETED"
                assert result["outcome"] == "APPROVED"
                assert result["approval_id"] == approval_id
                assert result["decided_by"] == ctx.company_a.owner.user_id
                assert result["side_effect"]["action"] == "APPROVED_ACTION_EXECUTED"

                # Confirm final query status
                final_status = await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
                assert final_status == "COMPLETED_APPROVED"
    finally:
        temporal_core.set_temporal_client(None)


# ==============================================================================
# 2. Rejection Scenario
# ==============================================================================


@pytest.mark.asyncio
async def test_02_rejection_scenario(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Mandatory Test Scenario 2:
    - Start dummy workflow.
    - Confirm workflow is waiting.
    - Reject from the application with a mandatory reason.
    - Confirm workflow resumes, executes rejection cleanup activity, and completes with outcome=REJECTED.
    """
    ctx = isolation_context
    headers_employee = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    headers_owner = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    create_resp = await async_client.post(
        "/api/v1/approvals",
        headers=headers_employee,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-15",
                "end_date": "2026-12-16",
                "days_count": 2.0,
                "reason": "Personal commitments.",
            },
            "approver_role": "owner",
        },
    )
    assert create_resp.status_code == 201
    approval_id = create_resp.json()["id"]

    env = await get_test_environment()
    temporal_core.set_temporal_client(env.client)
    task_queue = f"tq-rejection-{approval_id}"
    workflow_id = f"dummy-approval-{approval_id}"

    try:
        async with (
            env,
            Worker(
                env.client,
                task_queue=task_queue,
                workflows=[DummyApprovalWorkflow, WaitForApprovalWorkflow],
                activities=[
                    verify_approval_record_activity,
                    execute_dummy_approved_action_activity,
                    execute_dummy_rejected_action_activity,
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                DummyApprovalWorkflow.run,
                DummyWorkflowInput(
                    workflow_id=workflow_id,
                    approval_id=approval_id,
                    tenant_id=ctx.company_a.tenant_id,
                ),
                id=workflow_id,
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=45),
            )

            assert (
                await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
                == "WAITING_FOR_APPROVAL"
            )

            # Reject from application
            reject_resp = await async_client.post(
                f"/api/v1/approvals/{approval_id}/reject",
                headers=headers_owner,
                json={"reason": "Insufficient project coverage during release week."},
            )
            assert reject_resp.status_code == 200
            assert reject_resp.json()["status"] == "rejected"

            # Confirm workflow completes with REJECTED
            result = await handle.result()
            assert result["status"] == "COMPLETED"
            assert result["outcome"] == "REJECTED"
            assert result["approval_id"] == approval_id
            assert (
                result["rejection_reason"] == "Insufficient project coverage during release week."
            )
            assert result["side_effect"]["action"] == "REJECTED_CLEANUP_EXECUTED"

            assert (
                await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
                == "COMPLETED_REJECTED"
            )
    finally:
        temporal_core.set_temporal_client(None)


# ==============================================================================
# 3. Duplicate Signal Prevention
# ==============================================================================


@pytest.mark.asyncio
async def test_03_duplicate_signal_prevention(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Mandatory Test Scenario 3:
    - Multiple duplicate approval signals are dispatched to a waiting workflow.
    - Enforces idempotency: only the first signal is processed; duplicate signals are discarded.
    - Side-effect activities execute strictly once.
    """
    ctx = isolation_context
    headers_employee = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    headers_owner = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    create_resp = await async_client.post(
        "/api/v1/approvals",
        headers=headers_employee,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "sick",
                "start_date": "2026-12-20",
                "end_date": "2026-12-21",
                "days_count": 2.0,
                "reason": "Medical recovery.",
            },
            "approver_role": "owner",
        },
    )
    assert create_resp.status_code == 201
    approval_id = create_resp.json()["id"]

    # Commit approval to database
    approve_resp = await async_client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers=headers_owner,
        json={"reason": "Approved valid medical leave."},
    )
    assert approve_resp.status_code == 200

    env = await get_test_environment()
    temporal_core.set_temporal_client(env.client)
    task_queue = f"tq-duplicate-{approval_id}"
    workflow_id = f"dummy-approval-{approval_id}"

    try:
        async with (
            env,
            Worker(
                env.client,
                task_queue=task_queue,
                workflows=[DummyApprovalWorkflow, WaitForApprovalWorkflow],
                activities=[
                    verify_approval_record_activity,
                    execute_dummy_approved_action_activity,
                    execute_dummy_rejected_action_activity,
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                DummyApprovalWorkflow.run,
                DummyWorkflowInput(
                    workflow_id=workflow_id,
                    approval_id=approval_id,
                    tenant_id=ctx.company_a.tenant_id,
                ),
                id=workflow_id,
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=45),
            )

            assert (
                await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
                == "WAITING_FOR_APPROVAL"
            )

            # Send first signal to the primary workflow
            sig1 = ApprovalDecisionSignal(
                approval_id=approval_id,
                action="approved",
                decided_by=ctx.company_a.owner.user_id,
                tenant_id=ctx.company_a.tenant_id,
                reason="Signal 1: Primary decision.",
            )
            await handle.signal("approval_decision_signal", sig1)

            # Immediately send duplicate signals
            sig2 = ApprovalDecisionSignal(
                approval_id=approval_id,
                action="approved",
                decided_by=ctx.company_a.owner.user_id,
                tenant_id=ctx.company_a.tenant_id,
                reason="Signal 2: Duplicate re-transmission.",
            )
            await handle.signal("approval_decision_signal", sig2)

            sig3 = ApprovalDecisionSignal(
                approval_id=approval_id,
                action="rejected",
                decided_by=ctx.company_a.owner.user_id,
                tenant_id=ctx.company_a.tenant_id,
                reason="Signal 3: Conflicting late signal.",
            )
            await handle.signal("approval_decision_signal", sig3)

            result = await handle.result()
            assert result["status"] == "COMPLETED"
            assert result["outcome"] == "APPROVED"
            assert result["decision_reason"] == "Signal 1: Primary decision."
            assert result["side_effect"]["action"] == "APPROVED_ACTION_EXECUTED"
    finally:
        temporal_core.set_temporal_client(None)


# ==============================================================================
# 4. Unauthorized Decision Defense (Correction 2: Keep Waiting on Spoofed Signals)
# ==============================================================================


@pytest.mark.asyncio
async def test_04_unauthorized_decision_defense(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Mandatory Test Scenario 4 (Correction 2):
    - An attacker or spoofed process sends an approval signal while the database record remains PENDING.
    - verify_approval_record_activity checks the authoritative database and detects the status mismatch.
    - The workflow discards the unverified decision, logs a security warning, and KEEPS WAITING.
    - Does not execute approved side effects for spoofed signals.
    - When legitimate owner approves via API, workflow unblocks, verifies against PostgreSQL, and completes.
    """
    ctx = isolation_context
    headers_employee = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    headers_owner = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    create_resp = await async_client.post(
        "/api/v1/approvals",
        headers=headers_employee,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "sick",
                "start_date": "2026-12-25",
                "end_date": "2026-12-26",
                "days_count": 2.0,
                "reason": "Illness.",
            },
            "approver_role": "owner",
        },
    )
    assert create_resp.status_code == 201
    approval_id = create_resp.json()["id"]
    # Approval remains 'pending' in database — never approved by owner!

    env = await get_test_environment()
    temporal_core.set_temporal_client(env.client)
    task_queue = f"tq-unauthorized-{approval_id}"
    workflow_id = f"dummy-approval-{approval_id}"

    try:
        async with (
            env,
            Worker(
                env.client,
                task_queue=task_queue,
                workflows=[DummyApprovalWorkflow, WaitForApprovalWorkflow],
                activities=[
                    verify_approval_record_activity,
                    execute_dummy_approved_action_activity,
                    execute_dummy_rejected_action_activity,
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                DummyApprovalWorkflow.run,
                DummyWorkflowInput(
                    workflow_id=workflow_id,
                    approval_id=approval_id,
                    tenant_id=ctx.company_a.tenant_id,
                ),
                id=workflow_id,
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=45),
            )

            assert (
                await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
                == "WAITING_FOR_APPROVAL"
            )

            # Step 1: Send spoofed approval signal claiming it was approved by owner
            spoofed_signal = ApprovalDecisionSignal(
                approval_id=approval_id,
                action="approved",
                decided_by=ctx.company_a.owner.user_id,
                tenant_id=ctx.company_a.tenant_id,
                reason="Spoofed approval signal.",
            )
            await handle.signal("approval_decision_signal", spoofed_signal)

            # Step 2: Confirm workflow remains RUNNING and in WAITING_FOR_APPROVAL status (Correction 2)
            desc = await handle.describe(rpc_timeout=timedelta(seconds=5))
            assert desc.status == WorkflowExecutionStatus.RUNNING

            status = await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
            assert status == "WAITING_FOR_APPROVAL"

            # Step 3: Legitimate approval is now committed via API
            approve_resp = await async_client.post(
                f"/api/v1/approvals/{approval_id}/approve",
                headers=headers_owner,
                json={"reason": "Legitimate approval after thorough review."},
            )
            assert approve_resp.status_code == 200, approve_resp.text
            assert approve_resp.json()["status"] == "approved"

            # Step 4: Workflow unblocks, verifies genuine decision against PostgreSQL, and completes
            result = await handle.result()
            assert result["status"] == "COMPLETED"
            assert result["outcome"] == "APPROVED"
            assert result["decided_by"] == ctx.company_a.owner.user_id
            assert result["side_effect"]["action"] == "APPROVED_ACTION_EXECUTED"

            assert (
                await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
                == "COMPLETED_APPROVED"
            )
    finally:
        temporal_core.set_temporal_client(None)


# ==============================================================================
# 5. Identifiers Only (ADR 003 Privacy Boundary)
# ==============================================================================


@pytest.mark.asyncio
async def test_05_identifiers_only_privacy(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Mandatory Test Scenario 5:
    - Creates an approval with sensitive candidate salary (PKR 450,000) using valid CandidateOfferPayload schema.
    - Initiates workflow passing only identifiers (approval_id, tenant_id).
    - Inspects full Temporal workflow event history.
    - Asserts that sensitive payload strings never appear in Temporal history.
    """
    ctx = isolation_context
    headers_recruiter = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}
    headers_owner = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    sensitive_salary_pkr = 450000
    candidate_email = "tariq.mahmood@example.com"

    create_resp = await async_client.post(
        "/api/v1/approvals",
        headers=headers_recruiter,
        json={
            "approval_type": "candidate_offer",
            "payload": {
                "candidate_name": "Tariq Mahmood",
                "candidate_email": candidate_email,
                "position": "Principal Architect",
                "offered_salary_pkr": sensitive_salary_pkr,
                "start_date": "2026-12-01",
            },
            "approver_role": "owner",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    approval_id = create_resp.json()["id"]

    env = await get_test_environment()
    temporal_core.set_temporal_client(env.client)
    task_queue = f"tq-privacy-{approval_id}"
    workflow_id = f"dummy-approval-{approval_id}"

    try:
        async with (
            env,
            Worker(
                env.client,
                task_queue=task_queue,
                workflows=[DummyApprovalWorkflow, WaitForApprovalWorkflow],
                activities=[
                    verify_approval_record_activity,
                    execute_dummy_approved_action_activity,
                    execute_dummy_rejected_action_activity,
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                DummyApprovalWorkflow.run,
                DummyWorkflowInput(
                    workflow_id=workflow_id,
                    approval_id=approval_id,
                    tenant_id=ctx.company_a.tenant_id,
                ),
                id=workflow_id,
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=45),
            )

            assert (
                await handle.query("get_status", rpc_timeout=timedelta(seconds=5))
                == "WAITING_FOR_APPROVAL"
            )

            # Approve from application
            approve_resp = await async_client.post(
                f"/api/v1/approvals/{approval_id}/approve",
                headers=headers_owner,
                json={"reason": "Executive offer approved."},
            )
            assert approve_resp.status_code == 200

            result = await handle.result()
            assert result["status"] == "COMPLETED"
            assert result["outcome"] == "APPROVED"

            # Fetch entire event history of the dummy workflow
            history = await handle.fetch_history()
            history_str = str(history)

            # Verify identifier is present
            assert approval_id in history_str

            # CRITICAL PRIVACY ASSERTION (ADR 003): Sensitive data must NOT leak into Temporal history
            assert str(sensitive_salary_pkr) not in history_str, (
                "Confidential salary PKR leaked into Temporal workflow history!"
            )
            assert candidate_email not in history_str, (
                "Confidential candidate email leaked into Temporal workflow history!"
            )
    finally:
        temporal_core.set_temporal_client(None)
