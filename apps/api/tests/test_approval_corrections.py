"""
Comprehensive Automated Test Suite for Approval Corrections and Multi-Round Resubmissions.
Complies with Stage 2 specifications.

Verifies the 14 mandatory scenarios:
1. Normal approval.
2. Request changes.
3. Requester edits and resubmits.
4. Approver reviews the corrected version.
5. Multiple correction rounds (Rev 1 -> Rev 2 -> Rev 3).
6. Permanent rejection.
7. Unauthorized editing.
8. Unauthorized approval.
9. Cross-tenant correction attempts.
10. Duplicate resubmission / duplicate decision.
11. Audit history for every revision.
12. Payroll correction and recalculation.
13. Material edit requires renewed approval.
14. Anti-self-approval rule enforcement.
"""

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
async def test_01_normal_approval(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """1. Verifies normal approval flow: pending -> approved with audit logging."""
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
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["status"] == "pending"
    assert created["current_revision"] == 1

    # Manager approves
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}
    appr_resp = await async_client.post(
        f"/api/v1/approvals/{created['id']}/approve",
        headers=mgr_headers,
        json={"reason": "Approved as per policy."},
    )
    assert appr_resp.status_code == 200, appr_resp.text
    approved = appr_resp.json()
    assert approved["status"] == "approved"
    assert approved["decided_by"] == ctx.company_a.manager.user_id


@pytest.mark.asyncio
async def test_02_request_changes(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """2. Verifies request changes requires instructions and transitions to changes_requested."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}

    payload = {
        "approval_type": "leave_request",
        "payload": {
            "leave_type": "annual",
            "start_date": "2026-12-15",
            "end_date": "2026-12-18",
            "days_count": 4.0,
            "reason": "Annual planned vacation.",
        },
        "approver_role": "manager",
    }
    resp = await async_client.post("/api/v1/approvals", headers=headers, json=payload)
    assert resp.status_code == 201
    created = resp.json()

    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Attempt without reason (or empty string) -> 422 Unprocessable Entity
    bad_req = await async_client.post(
        f"/api/v1/approvals/{created['id']}/request-changes",
        headers=mgr_headers,
        json={"reason": "  "},
    )
    assert bad_req.status_code == 422

    # Valid request changes with instructions
    valid_req = await async_client.post(
        f"/api/v1/approvals/{created['id']}/request-changes",
        headers=mgr_headers,
        json={"reason": "Please adjust start date to December 16 to ensure project coverage."},
    )
    assert valid_req.status_code == 200, valid_req.text
    changes = valid_req.json()
    assert changes["status"] == "changes_requested"
    assert (
        changes["decision_reason"]
        == "Please adjust start date to December 16 to ensure project coverage."
    )
    assert changes["decided_by"] == ctx.company_a.manager.user_id


@pytest.mark.asyncio
async def test_03_requester_edits_and_resubmits(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """3. Verifies requester edits payload, resubmits, and revision increments to 2."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # 1. Create
    resp = await async_client.post(
        "/api/v1/approvals",
        headers=emp_headers,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "sick",
                "start_date": "2026-12-01",
                "end_date": "2026-12-02",
                "days_count": 2.0,
                "reason": "Doctor appointment.",
            },
            "approver_role": "manager",
        },
    )
    assert resp.status_code == 201
    appr_id = resp.json()["id"]

    # 2. Manager requests changes
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=mgr_headers,
        json={"reason": "Attach medical clearance certificate details in reason."},
    )

    # 3. Requester resubmits
    resubmit_resp = await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=emp_headers,
        json={
            "payload": {
                "leave_type": "sick",
                "start_date": "2026-12-01",
                "end_date": "2026-12-02",
                "days_count": 2.0,
                "reason": "Doctor appointment. Attached certificate ref #MC-9941.",
            }
        },
    )
    assert resubmit_resp.status_code == 200, resubmit_resp.text
    resubmitted = resubmit_resp.json()
    assert resubmitted["status"] == "pending"
    assert resubmitted["current_revision"] == 2
    assert (
        resubmitted["payload"]["reason"] == "Doctor appointment. Attached certificate ref #MC-9941."
    )
    assert resubmitted["decided_by"] is None
    assert resubmitted["decision_reason"] is None


@pytest.mark.asyncio
async def test_04_approver_reviews_corrected_version(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """4. Verifies approver inspects the corrected revision 2 and commits approval."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Create & Request changes & Resubmit
    init = (
        await async_client.post(
            "/api/v1/approvals",
            headers=emp_headers,
            json={
                "approval_type": "leave_request",
                "payload": {
                    "leave_type": "annual",
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-05",
                    "days_count": 5.0,
                    "reason": "Winter break.",
                },
                "approver_role": "manager",
            },
        )
    ).json()

    await async_client.post(
        f"/api/v1/approvals/{init['id']}/request-changes",
        headers=mgr_headers,
        json={"reason": "Reduce to 3 days due to quarterly sprint."},
    )

    await async_client.post(
        f"/api/v1/approvals/{init['id']}/resubmit",
        headers=emp_headers,
        json={
            "payload": {
                "leave_type": "annual",
                "start_date": "2026-12-01",
                "end_date": "2026-12-03",
                "days_count": 3.0,
                "reason": "Winter break adjusted to 3 business days.",
            }
        },
    )

    # Manager fetches details and checks revision history
    details = (
        await async_client.get(f"/api/v1/approvals/{init['id']}", headers=mgr_headers)
    ).json()
    assert details["current_revision"] == 2
    assert len(details["revisions"]) == 2

    # Manager approves Rev 2
    appr = await async_client.post(
        f"/api/v1/approvals/{init['id']}/approve",
        headers=mgr_headers,
        json={"reason": "Looks good now."},
    )
    assert appr.status_code == 200
    assert appr.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_05_multiple_correction_rounds(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """5. Verifies multiple correction rounds: Rev 1 -> Rev 2 -> Rev 3 with full history preserved."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Round 1
    resp = await async_client.post(
        "/api/v1/approvals",
        headers=emp_headers,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-12",
                "days_count": 3.0,
                "reason": "Round 1 initial draft.",
            },
            "approver_role": "manager",
        },
    )
    assert resp.status_code == 201
    appr_id = resp.json()["id"]

    # Round 1 -> Changes
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=mgr_headers,
        json={"reason": "First correction request."},
    )

    # Round 2 Resubmit
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=emp_headers,
        json={
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-11",
                "days_count": 2.0,
                "reason": "Round 2 adjusted draft.",
            }
        },
    )

    # Round 2 -> Changes again
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=mgr_headers,
        json={"reason": "Second correction request: change to 1 day."},
    )

    # Round 3 Resubmit
    resub3 = await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=emp_headers,
        json={
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-10",
                "days_count": 1.0,
                "reason": "Round 3 final draft.",
            }
        },
    )
    assert resub3.status_code == 200
    assert resub3.json()["current_revision"] == 3

    # Manager approves Round 3
    final_appr = await async_client.post(
        f"/api/v1/approvals/{appr_id}/approve",
        headers=mgr_headers,
        json={"reason": "Final approval."},
    )
    assert final_appr.status_code == 200

    # Verify revisions history endpoint returns all 3 distinct rounds
    revs_resp = await async_client.get(
        f"/api/v1/approvals/{appr_id}/revisions", headers=mgr_headers
    )
    assert revs_resp.status_code == 200
    revisions = revs_resp.json()
    assert len(revisions) == 3
    assert revisions[0]["revision_number"] == 1
    assert revisions[0]["decision"] == "changes_requested"
    assert revisions[0]["payload"]["reason"] == "Round 1 initial draft."

    assert revisions[1]["revision_number"] == 2
    assert revisions[1]["decision"] == "changes_requested"
    assert revisions[1]["payload"]["reason"] == "Round 2 adjusted draft."

    assert revisions[2]["revision_number"] == 3
    assert revisions[2]["decision"] == "approved"
    assert revisions[2]["payload"]["reason"] == "Round 3 final draft."


@pytest.mark.asyncio
async def test_06_permanent_rejection(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """6. Verifies permanent rejection is terminal: cannot resubmit or decide again."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    resp = await async_client.post(
        "/api/v1/approvals",
        headers=emp_headers,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-12",
                "days_count": 3.0,
                "reason": "Test rejection.",
            },
            "approver_role": "manager",
        },
    )
    appr_id = resp.json()["id"]

    # Reject
    rej_resp = await async_client.post(
        f"/api/v1/approvals/{appr_id}/reject",
        headers=mgr_headers,
        json={"reason": "Denied due to team blackout period."},
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["status"] == "rejected"

    # Attempt to resubmit -> 409 Conflict
    resub = await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=emp_headers,
        json={
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-12",
                "days_count": 3.0,
                "reason": "Try again.",
            }
        },
    )
    assert resub.status_code == 409


@pytest.mark.asyncio
async def test_07_unauthorized_editing(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """7. Verifies user B cannot edit and resubmit user A's returned draft -> 403 Forbidden."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    resp = await async_client.post(
        "/api/v1/approvals",
        headers=emp_headers,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-12",
                "days_count": 3.0,
                "reason": "Original requester leave.",
            },
            "approver_role": "owner",
        },
    )
    appr_id = resp.json()["id"]

    # Owner requests changes
    owner_headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=owner_headers,
        json={"reason": "Please fix dates."},
    )

    # Manager (who is NOT the requester) attempts to resubmit draft -> 403 Forbidden
    tamper = await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=mgr_headers,
        json={
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-11",
                "end_date": "2026-12-12",
                "days_count": 2.0,
                "reason": "Manager tampering with employee draft.",
            }
        },
    )
    assert tamper.status_code == 403, f"Expected 403, got {tamper.status_code}: {tamper.text}"


@pytest.mark.asyncio
async def test_08_unauthorized_approval(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """8. Verifies ordinary employee cannot decide or request changes -> 403 Forbidden."""
    ctx = isolation_context
    owner_headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}

    resp = await async_client.post(
        "/api/v1/approvals",
        headers=owner_headers,
        json={
            "approval_type": "candidate_offer",
            "payload": {
                "candidate_name": "Bob Vance",
                "candidate_email": "bob.vance@example.com",
                "position": "Refrigeration Specialist",
                "offered_salary_pkr": 180000,
                "start_date": "2026-12-01",
            },
            "approver_role": "owner",
        },
    )
    appr_id = resp.json()["id"]

    # Employee tries to request changes -> 403
    t1 = await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=emp_headers,
        json={"reason": "Unauthorized changes request."},
    )
    assert t1.status_code == 403

    # Employee tries to approve -> 403
    t2 = await async_client.post(
        f"/api/v1/approvals/{appr_id}/approve",
        headers=emp_headers,
        json={"reason": "Unauthorized approval."},
    )
    assert t2.status_code == 403


@pytest.mark.asyncio
async def test_09_cross_tenant_correction_attempts(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """9. Verifies Company B cannot view, request changes, or resubmit Company A draft -> 404."""
    ctx = isolation_context
    emp_a_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    owner_b_headers = {"Authorization": f"Bearer {ctx.company_b.owner.token}"}

    resp = await async_client.post(
        "/api/v1/approvals",
        headers=emp_a_headers,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "sick",
                "start_date": "2026-12-01",
                "end_date": "2026-12-02",
                "days_count": 2.0,
                "reason": "Company A confidential leave.",
            },
            "approver_role": "manager",
        },
    )
    appr_id = resp.json()["id"]

    # Company B owner tries to view -> 404
    get_resp = await async_client.get(f"/api/v1/approvals/{appr_id}", headers=owner_b_headers)
    assert get_resp.status_code == 404

    # Company B owner tries to request changes -> 404
    rc_resp = await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=owner_b_headers,
        json={"reason": "Cross-tenant intrusion attempt."},
    )
    assert rc_resp.status_code == 404

    # Company B owner tries to resubmit -> 404
    resub_resp = await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=owner_b_headers,
        json={
            "payload": {
                "leave_type": "sick",
                "start_date": "2026-12-01",
                "end_date": "2026-12-02",
                "days_count": 2.0,
                "reason": "Injected payload.",
            }
        },
    )
    assert resub_resp.status_code == 404


@pytest.mark.asyncio
async def test_10_duplicate_resubmission_and_decision(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """10. Verifies duplicate resubmission or decision returns 409 Conflict."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    resp = await async_client.post(
        "/api/v1/approvals",
        headers=emp_headers,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "annual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-12",
                "days_count": 3.0,
                "reason": "Test duplicate.",
            },
            "approver_role": "manager",
        },
    )
    appr_id = resp.json()["id"]

    # 1. Attempt resubmit while still 'pending' -> 409
    dup_resub = await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=emp_headers,
        json={
            "payload": {
                "leave_type": "annual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-12",
                "days_count": 3.0,
                "reason": "Test duplicate.",
            }
        },
    )
    assert dup_resub.status_code == 409

    # 2. Manager approves
    await async_client.post(f"/api/v1/approvals/{appr_id}/approve", headers=mgr_headers)

    # 3. Attempt second approval on already approved request -> 409
    dup_appr = await async_client.post(f"/api/v1/approvals/{appr_id}/approve", headers=mgr_headers)
    assert dup_appr.status_code == 409


@pytest.mark.asyncio
async def test_11_audit_history_for_every_revision(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """11. Verifies audit log records are generated for create, request_changes, resubmit, and approve."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}
    owner_headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Create
    init = (
        await async_client.post(
            "/api/v1/approvals",
            headers=emp_headers,
            json={
                "approval_type": "leave_request",
                "payload": {
                    "leave_type": "casual",
                    "start_date": "2026-12-10",
                    "end_date": "2026-12-12",
                    "days_count": 3.0,
                    "reason": "Audit verification flow.",
                },
                "approver_role": "manager",
            },
        )
    ).json()
    appr_id = init["id"]

    # Request Changes
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=mgr_headers,
        json={"reason": "Audit check instruction."},
    )

    # Resubmit
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=emp_headers,
        json={
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-10",
                "end_date": "2026-12-11",
                "days_count": 2.0,
                "reason": "Audit check amended.",
            }
        },
    )

    # Approve
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/approve",
        headers=mgr_headers,
        json={"reason": "Audited approval."},
    )

    # Inspect audit logs
    audit_resp = await async_client.get(
        "/api/v1/audit/logs",
        headers=owner_headers,
        params={"target_id": appr_id},
    )
    assert audit_resp.status_code == 200
    logs = audit_resp.json()
    actions = [log["action"] for log in logs]
    assert "approval.create" in actions
    assert "approval.request_changes" in actions
    assert "approval.resubmit" in actions
    assert "approval.approved" in actions


@pytest.mark.asyncio
async def test_12_payroll_correction_and_recalculation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """12. Verifies payroll override calculates and recalculates net adjustment on resubmission."""
    ctx = isolation_context
    owner_headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Initial submission: Bonus 50,000, Deduction 10,000 -> Net +40,000
    init_resp = await async_client.post(
        "/api/v1/approvals",
        headers=owner_headers,
        json={
            "approval_type": "payroll_override",
            "payload": {
                "employee_id": "EMP-001",
                "employee_name": "Hamza Ali",
                "bonus_pkr": 50000,
                "deduction_pkr": 10000,
                "justification": "Performance bonus V1.",
            },
            "approver_role": "payroll_admin",
        },
    )
    assert init_resp.status_code == 201
    init_data = init_resp.json()
    assert init_data["payload"]["net_adjustment_pkr"] == 40000

    # Approver requests changes: "Reduce bonus to 30,000"
    appr_id = init_data["id"]
    await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=owner_headers,
        json={"reason": "Reduce bonus to 30,000 as per budget limits."},
    )

    # Resubmission: Bonus 30,000, Deduction 5,000 -> Recalculated Net +25,000
    resub_resp = await async_client.post(
        f"/api/v1/approvals/{appr_id}/resubmit",
        headers=owner_headers,
        json={
            "payload": {
                "employee_id": "EMP-001",
                "employee_name": "Hamza Ali",
                "bonus_pkr": 30000,
                "deduction_pkr": 5000,
                "justification": "Budget compliant performance adjustment.",
            }
        },
    )
    assert resub_resp.status_code == 200
    resub_data = resub_resp.json()
    assert resub_data["payload"]["net_adjustment_pkr"] == 25000
    assert resub_data["current_revision"] == 2


@pytest.mark.asyncio
async def test_13_material_edit_renewed_approval(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """13. Verifies that resubmitting a material edit does NOT auto-approve; forces new approval round."""
    ctx = isolation_context
    emp_headers = {"Authorization": f"Bearer {ctx.company_a.employee.token}"}
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Create & Request changes
    init = (
        await async_client.post(
            "/api/v1/approvals",
            headers=emp_headers,
            json={
                "approval_type": "leave_request",
                "payload": {
                    "leave_type": "annual",
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-05",
                    "days_count": 5.0,
                    "reason": "Original vacation.",
                },
                "approver_role": "manager",
            },
        )
    ).json()

    await async_client.post(
        f"/api/v1/approvals/{init['id']}/request-changes",
        headers=mgr_headers,
        json={"reason": "Reduce days."},
    )

    # Resubmit
    resub = await async_client.post(
        f"/api/v1/approvals/{init['id']}/resubmit",
        headers=emp_headers,
        json={
            "payload": {
                "leave_type": "annual",
                "start_date": "2026-12-01",
                "end_date": "2026-12-02",
                "days_count": 2.0,
                "reason": "Adjusted vacation.",
            }
        },
    )
    # Status MUST remain pending, not approved
    assert resub.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_14_anti_self_approval_rule(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """14. Verifies non-owner requester cannot approve or request changes on their own request."""
    ctx = isolation_context
    mgr_headers = {"Authorization": f"Bearer {ctx.company_a.manager.token}"}

    # Manager submits a request where approver_role is 'manager'
    resp = await async_client.post(
        "/api/v1/approvals",
        headers=mgr_headers,
        json={
            "approval_type": "leave_request",
            "payload": {
                "leave_type": "casual",
                "start_date": "2026-12-20",
                "end_date": "2026-12-21",
                "days_count": 2.0,
                "reason": "Manager personal leave.",
            },
            "approver_role": "manager",
        },
    )
    appr_id = resp.json()["id"]

    # Same manager attempts self-approval -> 403 Forbidden
    self_appr = await async_client.post(
        f"/api/v1/approvals/{appr_id}/approve",
        headers=mgr_headers,
        json={"reason": "Self approving."},
    )
    assert self_appr.status_code == 403

    # Same manager attempts self request-changes -> 403 Forbidden
    self_rc = await async_client.post(
        f"/api/v1/approvals/{appr_id}/request-changes",
        headers=mgr_headers,
        json={"reason": "Self request changes."},
    )
    assert self_rc.status_code == 403
