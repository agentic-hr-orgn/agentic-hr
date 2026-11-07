"""
Comprehensive Automated Test Suite for Human Approval System.
Complies with Stage 2 Phase 6 specifications.

Verifies:
1. Authorized approval.
2. Unauthorized approval (role and permission flags).
3. Wrong tenant isolation (PostgreSQL RLS fail-closed).
4. Duplicate decision rejection (terminal state).
5. Invalid status transition.
6. Missing rejection reason where required.
7. Anti-self-approval rule enforcement.
8. Audit record generation for creation and decisions.
9. Concurrency row-level locking.
"""

import asyncio

import httpx
import pytest

from app.core.clerk import register_test_signing_key
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


@pytest.mark.asyncio
async def test_01_create_approval_leave_request_valid(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """1. Verifies submitting a valid leave request: status defaults to pending, audit log written."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}

    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "casual",
            "start_date": "2026-12-10",
            "end_date": "2026-12-12",
            "days_count": 3.0,
            "reason": "Family emergency and urgent commitments.",
        },
        "approver_role": "manager",
    }

    resp = await async_client.post("/api/v1/approvals", headers=headers, json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["status"] == "pending"
    assert data["approval_type"] == "leave_request"
    assert data["approver_role"] == "manager"
    assert data["requester_id"] == ctx.company_a.employee.user_id
    assert data["tenant_id"] == ctx.company_a.tenant_id
    assert data["decided_by"] is None
    assert data["decided_at"] is None

    # Verify audit log was recorded
    owner_headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    audit_resp = await async_client.get(
        "/api/v1/audit/logs",
        headers=owner_headers,
        params={"target_id": data["id"]},
    )
    assert audit_resp.status_code == 200
    audit_logs = audit_resp.json()
    assert len(audit_logs) >= 1
    create_log = audit_logs[0]
    assert create_log["action"] == "approval.create"
    assert create_log["actor_id"] == ctx.company_a.employee.user_id


@pytest.mark.asyncio
async def test_02_create_approval_payload_validation_failures(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """2. Verifies that invalid payloads are rejected with HTTP 422 Unprocessable Entity."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}

    # Case A: Date order error (end_date < start_date)
    bad_dates = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "annual",
            "start_date": "2026-12-20",
            "end_date": "2026-12-15",
            "days_count": 5.0,
            "reason": "Invalid dates test",
        },
        "approver_role": "manager",
    }
    resp_a = await async_client.post("/api/v1/approvals", headers=headers, json=bad_dates)
    assert resp_a.status_code == 422

    # Case B: Candidate offer with zero salary
    bad_salary = {
        "approval_type": "candidate_offer",
        "payload": {
            "candidate_name": "Test Candidate",
            "candidate_email": "candidate@example.com",
            "position": "Software Engineer",
            "offered_salary_pkr": 0,
            "start_date": "2026-12-01",
        },
        "approver_role": "owner",
    }
    resp_b = await async_client.post("/api/v1/approvals", headers=headers, json=bad_salary)
    assert resp_b.status_code == 422

    # Case C: Payroll override with zero bonus and zero deduction
    bad_payroll = {
        "approval_type": "payroll_override",
        "payload": {
            "employee_id": "emp_123",
            "employee_name": "John Doe",
            "bonus_pkr": 0,
            "deduction_pkr": 0,
            "justification": "Invalid payroll adjustment amounts",
        },
        "approver_role": "owner",
    }
    resp_c = await async_client.post("/api/v1/approvals", headers=headers, json=bad_payroll)
    assert resp_c.status_code == 422


@pytest.mark.asyncio
async def test_03_list_and_filter_approvals(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """3. Verifies listing approvals and filtering by status."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Fetch all
    all_resp = await async_client.get("/api/v1/approvals", headers=headers)
    assert all_resp.status_code == 200
    all_items = all_resp.json()
    assert len(all_items) >= 4  # Synthetic seeded items

    # Filter pending
    pending_resp = await async_client.get("/api/v1/approvals?status=pending", headers=headers)
    assert pending_resp.status_code == 200
    pending_items = pending_resp.json()
    assert all(item["status"] == "pending" for item in pending_items)

    # Filter approved
    approved_resp = await async_client.get("/api/v1/approvals?status=approved", headers=headers)
    assert approved_resp.status_code == 200
    approved_items = approved_resp.json()
    assert all(item["status"] == "approved" for item in approved_items)


@pytest.mark.asyncio
async def test_04_authorized_approval_success(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """4. Verifies authorized manager approves a pending leave request successfully."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Create request
    create_payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "annual",
            "start_date": "2026-12-15",
            "end_date": "2026-12-18",
            "days_count": 4.0,
            "reason": "Authorized approval verification test.",
        },
        "approver_role": "manager",
    }
    create_resp = await async_client.post(
        "/api/v1/approvals", headers=emp_headers, json=create_payload
    )
    assert create_resp.status_code == 201
    approval_id = create_resp.json()["id"]

    # Manager approves
    approve_resp = await async_client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers=mgr_headers,
        json={"reason": "Approved by engineering manager."},
    )
    assert approve_resp.status_code == 200
    data = approve_resp.json()
    assert data["status"] == "approved"
    assert data["decided_by"] == ctx.company_a.manager.user_id
    assert data["decision_reason"] == "Approved by engineering manager."
    assert data["decided_at"] is not None

    # Verify audit log was recorded
    owner_headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    audit_resp = await async_client.get(
        "/api/v1/audit/logs",
        headers=owner_headers,
        params={"target_id": approval_id},
    )
    assert audit_resp.status_code == 200
    logs = audit_resp.json()
    decision_log = next((l for l in logs if l["action"] == "approval.approved"), None)
    assert decision_log is not None
    assert decision_log["actor_id"] == ctx.company_a.manager.user_id


@pytest.mark.asyncio
async def test_05_unauthorized_approval_denied(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """5. Verifies unauthorized roles are denied with HTTP 403 Forbidden."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # 1. Create request requiring manager
    req_payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "sick",
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
            "days_count": 2.0,
            "reason": "Medical recovery.",
        },
        "approver_role": "manager",
    }
    c_resp = await async_client.post("/api/v1/approvals", headers=emp_headers, json=req_payload)
    appr_id = c_resp.json()["id"]

    # Employee tries to approve -> 403 Forbidden
    emp_approve = await async_client.post(
        f"/api/v1/approvals/{appr_id}/approve", headers=emp_headers
    )
    assert emp_approve.status_code == 403
    assert "Access denied" in emp_approve.text

    # 2. Create request requiring owner
    owner_req = {
        "approval_type": "candidate_offer",
        "payload": {
            "candidate_name": "Senior Arch",
            "candidate_email": "arch@example.com",
            "position": "Enterprise Architect",
            "offered_salary_pkr": 600000,
            "start_date": "2026-12-01",
        },
        "approver_role": "owner",
    }
    c_resp_owner = await async_client.post("/api/v1/approvals", headers=mgr_headers, json=owner_req)
    owner_appr_id = c_resp_owner.json()["id"]

    # Manager tries to approve owner-level request -> 403 Forbidden
    mgr_approve = await async_client.post(
        f"/api/v1/approvals/{owner_appr_id}/approve", headers=mgr_headers
    )
    assert mgr_approve.status_code == 403


@pytest.mark.asyncio
async def test_06_wrong_tenant_isolation_blocked(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """6. Verifies Company B cannot view or decide Company A approvals (returns 404)."""
    ctx = isolation_context
    co_a_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    co_b_headers = {"Authorization": f"Bearer {ctx.company_b.owner.token}"}

    # Create approval in Company A
    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "casual",
            "start_date": "2026-12-05",
            "end_date": "2026-12-06",
            "days_count": 2.0,
            "reason": "Cross-tenant test.",
        },
        "approver_role": "manager",
    }
    resp = await async_client.post("/api/v1/approvals", headers=co_a_headers, json=payload)
    approval_id = resp.json()["id"]

    # Company B attempts to read Company A approval -> 404 Not Found
    get_cross = await async_client.get(f"/api/v1/approvals/{approval_id}", headers=co_b_headers)
    assert get_cross.status_code == 404

    # Company B attempts to approve Company A approval -> 404 Not Found
    approve_cross = await async_client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=co_b_headers
    )
    assert approve_cross.status_code == 404

    # Company B attempts to reject Company A approval -> 404 Not Found
    reject_cross = await async_client.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers=co_b_headers,
        json={"reason": "Malicious cross tenant rejection attempt."},
    )
    assert reject_cross.status_code == 404


@pytest.mark.asyncio
async def test_07_rejection_requires_reason(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """7. Verifies rejecting without a reason returns HTTP 422 Unprocessable Entity."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Create request
    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "casual",
            "start_date": "2026-12-05",
            "end_date": "2026-12-06",
            "days_count": 2.0,
            "reason": "Rejection reason test.",
        },
        "approver_role": "manager",
    }
    c_resp = await async_client.post("/api/v1/approvals", headers=emp_headers, json=payload)
    approval_id = c_resp.json()["id"]

    # Reject with empty string -> 422
    rej_empty = await async_client.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers=mgr_headers,
        json={"reason": ""},
    )
    assert rej_empty.status_code == 422

    # Reject with whitespace only -> 422
    rej_spaces = await async_client.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers=mgr_headers,
        json={"reason": "   "},
    )
    assert rej_spaces.status_code == 422


@pytest.mark.asyncio
async def test_08_valid_rejection_succeeds(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """8. Verifies valid rejection transitions status to rejected and stores reason & decider."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Create request
    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "casual",
            "start_date": "2026-12-07",
            "end_date": "2026-12-09",
            "days_count": 3.0,
            "reason": "Rejection flow test.",
        },
        "approver_role": "manager",
    }
    c_resp = await async_client.post("/api/v1/approvals", headers=emp_headers, json=payload)
    approval_id = c_resp.json()["id"]

    # Reject with valid reason
    rej_resp = await async_client.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers=mgr_headers,
        json={"reason": "Project deadline collision with sprint release."},
    )
    assert rej_resp.status_code == 200
    data = rej_resp.json()
    assert data["status"] == "rejected"
    assert data["decided_by"] == ctx.company_a.manager.user_id
    assert data["decision_reason"] == "Project deadline collision with sprint release."
    assert data["decided_at"] is not None

    # Verify audit log
    owner_headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    audit_resp = await async_client.get(
        "/api/v1/audit/logs",
        headers=owner_headers,
        params={"target_id": approval_id},
    )
    assert audit_resp.status_code == 200
    logs = audit_resp.json()
    reject_log = next((l for l in logs if l["action"] == "approval.rejected"), None)
    assert reject_log is not None
    assert reject_log["details"]["decision"] == "rejected"


@pytest.mark.asyncio
async def test_09_duplicate_decision_rejected(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """9. Verifies attempting to decide an already-decided approval returns HTTP 409 Conflict."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Create and approve request
    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "annual",
            "start_date": "2026-12-22",
            "end_date": "2026-12-24",
            "days_count": 3.0,
            "reason": "Duplicate decision test.",
        },
        "approver_role": "manager",
    }
    c_resp = await async_client.post("/api/v1/approvals", headers=emp_headers, json=payload)
    approval_id = c_resp.json()["id"]

    appr_1 = await async_client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=mgr_headers
    )
    assert appr_1.status_code == 200

    # Second approve attempt on same approval -> 409 Conflict
    appr_2 = await async_client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=mgr_headers
    )
    assert appr_2.status_code == 409
    assert "already been decided" in appr_2.text

    # Rejection attempt on already approved approval -> 409 Conflict
    rej_attempt = await async_client.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers=mgr_headers,
        json={"reason": "Late rejection attempt."},
    )
    assert rej_attempt.status_code == 409


@pytest.mark.asyncio
async def test_10_anti_self_approval_rule(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """10. Verifies non-owner requesters cannot decide their own approval requests."""
    ctx = isolation_context
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Manager submits request requiring manager
    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "annual",
            "start_date": "2026-12-28",
            "end_date": "2026-12-30",
            "days_count": 3.0,
            "reason": "Manager annual vacation.",
        },
        "approver_role": "manager",
    }
    c_resp = await async_client.post("/api/v1/approvals", headers=mgr_headers, json=payload)
    approval_id = c_resp.json()["id"]

    # That manager attempts to self-approve -> 403 Forbidden
    self_approve = await async_client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=mgr_headers
    )
    assert self_approve.status_code == 403
    assert "requesters cannot decide their own approval requests" in self_approve.text


@pytest.mark.asyncio
async def test_11_concurrent_decision_row_locking(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """11. Verifies concurrent decisions are serialized via row locking; only one succeeds."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}
    owner_headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Create request
    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "casual",
            "start_date": "2026-12-14",
            "end_date": "2026-12-15",
            "days_count": 2.0,
            "reason": "Concurrency row locking test.",
        },
        "approver_role": "manager",
    }
    c_resp = await async_client.post("/api/v1/approvals", headers=emp_headers, json=payload)
    approval_id = c_resp.json()["id"]

    # Execute concurrent decisions: manager approves, owner rejects
    task1 = async_client.post(f"/api/v1/approvals/{approval_id}/approve", headers=mgr_headers)
    task2 = async_client.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers=owner_headers,
        json={"reason": "Concurrent conflict rejection."},
    )

    resp1, resp2 = await asyncio.gather(task1, task2)

    statuses = {resp1.status_code, resp2.status_code}
    # Exactly one must succeed (200) and one must receive conflict (409)
    assert 200 in statuses, (
        f"Expected a 200 success, got {resp1.status_code} and {resp2.status_code}"
    )
    assert 409 in statuses, (
        f"Expected a 409 conflict, got {resp1.status_code} and {resp2.status_code}"
    )
