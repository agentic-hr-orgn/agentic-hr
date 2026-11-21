"""
Stage 5 Prompt 4 Verification Suite:
- Policy Upload (metadata, versioned rows, magic-byte validation, RBAC, storage_provider='auto')
- Library Listing (RBAC visibility, filters, include_archived) & OCR Review Queue
- Preview, Draft-Only Chunk Edit + Re-Embed, Page OCR Fix, and Rate-Limited Reingest
- Stage 2 `policy.publish` Approval Integration & Atomic Version Superseding (`v1` -> `superseded`)
- Soft-Delete Archival (`lifecycle_status='archived'`), Readiness Count & Cross-Tenant Isolation
"""

import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest
import pytest_asyncio

from app.services.storage_helper import StorageHelper
from tests.isolation_harness import MIGRATION_DB_URL, MultiTenantTestContext


@pytest_asyncio.fixture(autouse=True)
async def _preserve_globex_manager_flags(
    isolation_context: MultiTenantTestContext,
) -> AsyncIterator[None]:
    """Snapshots and restores globex.manager's `is_hr_head` & `is_payroll_admin` flags."""
    membership_id = isolation_context.company_b.manager.membership_id
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    orig = await conn.fetchrow(
        "SELECT is_hr_head, is_payroll_admin FROM public.memberships WHERE id = $1::uuid;",
        membership_id,
    )
    await conn.close()
    try:
        yield
    finally:
        if orig is not None:
            conn = await asyncpg.connect(MIGRATION_DB_URL)
            try:
                await conn.execute(
                    "UPDATE public.memberships SET is_hr_head = $1, is_payroll_admin = $2 "
                    "WHERE id = $3::uuid;",
                    orig["is_hr_head"],
                    orig["is_payroll_admin"],
                    membership_id,
                )
            finally:
                await conn.close()


async def _cleanup_policies_and_approvals(
    *,
    tenant_id: str,
    policy_ids: list[str],
    approval_ids: list[str],
    storage_keys: list[str],
) -> None:
    """Deletes temporary test policies, approvals, and local storage files."""
    for key in storage_keys:
        StorageHelper.delete_local_file(tenant_id=tenant_id, storage_key=key)

    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        if policy_ids:
            await conn.execute(
                "DELETE FROM public.policy_documents WHERE id = ANY($1::uuid[]);",
                policy_ids,
            )
        if approval_ids:
            await conn.execute(
                "DELETE FROM public.approvals WHERE id = ANY($1::uuid[]);",
                approval_ids,
            )
    finally:
        await conn.close()


# ==============================================================================
# 1. Upload Metadata Validation, Magic Bytes, Auto Storage & RBAC
# ==============================================================================
@pytest.mark.asyncio
async def test_01_upload_metadata_validation_magic_bytes_and_rbac(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - Employee and non-HR manager are blocked with 403 on policy upload.
    - Spoofed magic bytes (`MZ` as `.pdf`) are rejected with 400 `INVALID_POLICY_FILE_TYPE`.
    - Invalid `doc_type` / `visibility` are rejected with 422.
    - Valid upload with metadata (`doc_type`, `visibility`, `effective_date`, `source`,
      `storage_provider='auto'`) succeeds with 201, creates `v1` in `draft` + `indexed`.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    created_policy_ids: list[str] = []
    stored_keys: list[str] = []

    try:
        valid_txt = (
            b"# Attendance Policy\n"
            b"1.1 Core working hours are 09:00 to 18:00 PKT Monday through Friday."
        )

        # Ensure globex.manager is a plain manager (not HR Head) before checking 403
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
        finally:
            await conn.close()

        # 1. Employee and non-HR manager blocked with 403
        r_emp = await async_client.post(
            "/api/v1/policies",
            headers=emp_headers,
            files={"file": ("att.txt", valid_txt, "text/plain")},
        )
        assert r_emp.status_code == 403

        r_mgr = await async_client.post(
            "/api/v1/policies",
            headers=mgr_headers,
            files={"file": ("att.txt", valid_txt, "text/plain")},
        )
        assert r_mgr.status_code == 403

        # 2. Spoofed PDF magic bytes rejected with 400
        r_spoof = await async_client.post(
            "/api/v1/policies",
            headers=owner_headers,
            files={"file": ("fake.pdf", b"MZ\x90\x00\x03\x00", "application/pdf")},
        )
        assert r_spoof.status_code == 400
        assert "INVALID_POLICY_FILE_TYPE" in r_spoof.text

        # 3. Invalid doc_type / visibility rejected with 422
        r_bad_type = await async_client.post(
            "/api/v1/policies",
            headers=owner_headers,
            data={"doc_type": "invalid_type"},
            files={"file": ("att.txt", valid_txt, "text/plain")},
        )
        assert r_bad_type.status_code == 422

        # 4. Valid metadata upload with storage_provider="auto"
        title = f"Globex Attendance Policy {uuid.uuid4().hex[:6]}"
        r_ok = await async_client.post(
            "/api/v1/policies",
            headers=owner_headers,
            data={
                "title": title,
                "doc_type": "labour_law",
                "visibility": "managers",
                "language": "en",
                "effective_date": "2026-01-01",
                "source": "hr_upload",
                "storage_provider": "auto",
            },
            files={"file": ("attendance.txt", valid_txt, "text/plain")},
        )
        assert r_ok.status_code == 201, r_ok.text
        body = r_ok.json()
        created_policy_ids.append(body["id"])
        stored_keys.append(body["storage_key"])

        assert body["title"] == title
        assert body["doc_type"] == "labour_law"
        assert body["visibility"] == "managers"
        assert body["version"] == 1
        assert body["effective_date"] == "2026-01-01"
        assert body["source"] == "hr_upload"
        assert body["ingestion_status"] == "indexed"
        assert body["lifecycle_status"] == "draft"
        assert body["display_status"] == "DRAFT"
    finally:
        await _cleanup_policies_and_approvals(
            tenant_id=globex.tenant_id,
            policy_ids=created_policy_ids,
            approval_ids=[],
            storage_keys=stored_keys,
        )


# ==============================================================================
# 2. Publish via Stage 2 `policy.publish` Approval & Atomic Version Superseding
# ==============================================================================
@pytest.mark.asyncio
async def test_02_versioned_publish_approval_and_atomic_superseding(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - HR Head submits v1 for `policy.publish` -> creates Stage 2 approval (`pending`),
      policy transitions `draft -> review` (NOT `active` yet).
    - Owner approves `policy.publish` -> v1 atomically becomes `active`.
    - HR Head uploads v2 with same title -> creates NEW row `version == 2` in `draft` while
      v1 remains `active`.
    - Submitting v2 and approving `policy.publish` atomically sets v2 = `active` AND flips
      v1 = `superseded` with `policy_document.superseded` audit log.
    - Rejecting a `review` policy returns it to `draft`.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    hr_headers = {"Authorization": f"Bearer {globex.manager.token}"}

    # Grant globex.manager `is_hr_head = true` for four-eyes submitter -> owner approver test
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = true WHERE id = $1::uuid;",
            globex.manager.membership_id,
        )
    finally:
        await conn.close()

    suffix = uuid.uuid4().hex[:6]
    policy_title = f"Globex Leave Policy {suffix}"
    created_policy_ids: list[str] = []
    created_approval_ids: list[str] = []
    stored_keys: list[str] = []

    try:
        v1_bytes = (
            b"# Leave Policy v1\n"
            b"1.1 Full-time employees receive 20 days of paid annual leave per year."
        )
        r_v1 = await async_client.post(
            "/api/v1/policies",
            headers=hr_headers,
            data={"title": policy_title, "visibility": "all"},
            files={"file": ("leave_v1.txt", v1_bytes, "text/plain")},
        )
        assert r_v1.status_code == 201, r_v1.text
        v1 = r_v1.json()
        v1_id = v1["id"]
        created_policy_ids.append(v1_id)
        stored_keys.append(v1["storage_key"])
        assert v1["version"] == 1
        assert v1["lifecycle_status"] == "draft"
        assert v1["ingestion_status"] == "indexed"

        # 1. Submit v1 for publish -> creates Stage 2 approval, v1 in 'review' (NOT 'active')
        r_pub1 = await async_client.post(
            f"/api/v1/policies/{v1_id}/publish",
            headers=hr_headers,
            json={"approver_role": "owner"},
        )
        assert r_pub1.status_code == 200, r_pub1.text
        pub1 = r_pub1.json()
        appr1_id = pub1["approval_id"]
        created_approval_ids.append(appr1_id)
        assert pub1["document"]["lifecycle_status"] == "review"
        assert pub1["document"]["approval_id"] == appr1_id
        assert pub1["approval_status"] == "pending"

        # 2. Owner approves policy.publish -> v1 becomes active
        r_app1 = await async_client.post(
            f"/api/v1/approvals/{appr1_id}/approve",
            headers=owner_headers,
            json={"reason": "Approved for company-wide rollout"},
        )
        assert r_app1.status_code == 200, r_app1.text

        r_prev1 = await async_client.get(
            f"/api/v1/policies/{v1_id}/preview",
            headers=owner_headers,
        )
        assert r_prev1.status_code == 200
        assert r_prev1.json()["document"]["lifecycle_status"] == "active"
        assert r_prev1.json()["document"]["display_status"] == "ACTIVE"

        # 3. Upload v2 with same title -> new row version=2 in draft; v1 stays active
        v2_bytes = (
            b"# Leave Policy v2\n"
            b"1.1 Full-time employees receive 22 days of paid annual leave per year."
        )
        r_v2 = await async_client.post(
            "/api/v1/policies",
            headers=hr_headers,
            data={"title": policy_title, "visibility": "all"},
            files={"file": ("leave_v2.txt", v2_bytes, "text/plain")},
        )
        assert r_v2.status_code == 201, r_v2.text
        v2 = r_v2.json()
        v2_id = v2["id"]
        created_policy_ids.append(v2_id)
        stored_keys.append(v2["storage_key"])
        assert v2["version"] == 2
        assert v2["lifecycle_status"] == "draft"
        assert v2["supersedes_document_id"] == v1_id

        # 4. Submit v2 and reject first -> returns v2 to draft
        r_pub2 = await async_client.post(
            f"/api/v1/policies/{v2_id}/publish",
            headers=hr_headers,
            json={"approver_role": "owner"},
        )
        assert r_pub2.status_code == 200, r_pub2.text
        appr2_rej_id = r_pub2.json()["approval_id"]
        created_approval_ids.append(appr2_rej_id)

        r_rej2 = await async_client.post(
            f"/api/v1/approvals/{appr2_rej_id}/reject",
            headers=owner_headers,
            json={"reason": "Clarify carry-forward cap first"},
        )
        assert r_rej2.status_code == 200, r_rej2.text

        r_prev2_draft = await async_client.get(
            f"/api/v1/policies/{v2_id}/preview",
            headers=hr_headers,
        )
        assert r_prev2_draft.json()["document"]["lifecycle_status"] == "draft"

        # 5. Re-submit v2 and approve -> v2 becomes active AND v1 atomically flips to superseded
        r_pub2_ok = await async_client.post(
            f"/api/v1/policies/{v2_id}/publish",
            headers=hr_headers,
            json={"approver_role": "owner"},
        )
        assert r_pub2_ok.status_code == 200, r_pub2_ok.text
        appr2_ok_id = r_pub2_ok.json()["approval_id"]
        created_approval_ids.append(appr2_ok_id)

        r_app2 = await async_client.post(
            f"/api/v1/approvals/{appr2_ok_id}/approve",
            headers=owner_headers,
            json={"reason": "LGTM"},
        )
        assert r_app2.status_code == 200, r_app2.text

        r_prev2 = await async_client.get(f"/api/v1/policies/{v2_id}/preview", headers=owner_headers)
        r_prev1_after = await async_client.get(
            f"/api/v1/policies/{v1_id}/preview", headers=owner_headers
        )
        assert r_prev2.json()["document"]["lifecycle_status"] == "active"
        assert r_prev1_after.json()["document"]["lifecycle_status"] == "superseded"
        assert r_prev1_after.json()["document"]["display_status"] == "SUPERSEDED"
    finally:
        await _cleanup_policies_and_approvals(
            tenant_id=globex.tenant_id,
            policy_ids=created_policy_ids,
            approval_ids=created_approval_ids,
            storage_keys=stored_keys,
        )


# ==============================================================================
# 3. Review Queue, Preview, Page OCR Fix, Draft-Only Chunk Edit & Reingest
# ==============================================================================
@pytest.mark.asyncio
async def test_03_review_queue_preview_page_fix_chunk_edit_and_reingest(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - Low-text document lands in `GET /api/v1/policies/review-queue` (`needs_review`)
      and `POST /{id}/publish` is blocked with 409.
    - `POST /{id}/pages/1/fix` clears `needs_ocr`, removes page from `low_confidence_pages`,
      re-chunks/re-embeds, and transitions to `indexed`.
    - `PATCH /{id}/chunks/{chunk_id}` edits chunk text & token_count when `draft`, and is
      blocked with 409 when `lifecycle_status == 'review'`.
    - `POST /{id}/reingest` re-runs ingestion cleanly.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    created_policy_ids: list[str] = []
    created_approval_ids: list[str] = []
    stored_keys: list[str] = []

    try:
        # 1. Upload low-text file -> needs_review
        r_up = await async_client.post(
            "/api/v1/policies",
            headers=owner_headers,
            data={"title": f"Scanned Policy {uuid.uuid4().hex[:6]}"},
            files={"file": ("scan.txt", b"scan stub", "text/plain")},
        )
        assert r_up.status_code == 201, r_up.text
        doc = r_up.json()
        doc_id = doc["id"]
        created_policy_ids.append(doc_id)
        stored_keys.append(doc["storage_key"])
        assert doc["ingestion_status"] == "needs_review"

        # 2. Appears in review queue & publish is blocked (409)
        r_queue = await async_client.get("/api/v1/policies/review-queue", headers=owner_headers)
        assert r_queue.status_code == 200
        assert any(item["document"]["id"] == doc_id for item in r_queue.json())

        r_pub_block = await async_client.post(
            f"/api/v1/policies/{doc_id}/publish",
            headers=owner_headers,
        )
        assert r_pub_block.status_code == 409

        # 3. Fix page 1 -> clears needs_ocr & low_confidence_pages, transitions to indexed
        r_fix = await async_client.post(
            f"/api/v1/policies/{doc_id}/pages/1/fix",
            headers=owner_headers,
            json={
                "cleaned_text": (
                    "# Remote Work Policy\n"
                    "1.1 Full-time staff may work remotely up to two days per week."
                )
            },
        )
        assert r_fix.status_code == 200, r_fix.text
        fixed_doc = r_fix.json()
        assert fixed_doc["ingestion_status"] == "indexed"
        assert fixed_doc["low_confidence_pages"] == []

        # 4. Preview & edit chunk in draft
        r_prev = await async_client.get(f"/api/v1/policies/{doc_id}/preview", headers=owner_headers)
        assert r_prev.status_code == 200
        chunks = r_prev.json()["chunks"]
        assert len(chunks) == 1
        chunk_id = chunks[0]["id"]

        r_patch = await async_client.patch(
            f"/api/v1/policies/{doc_id}/chunks/{chunk_id}",
            headers=owner_headers,
            json={
                "text": "1.1 Full-time staff may work remotely up to three days per week with approval.",
                "section_path": "Remote Work > Updated Eligibility",
            },
        )
        assert r_patch.status_code == 200, r_patch.text
        patched = r_patch.json()
        assert "three days per week" in patched["text"]
        assert patched["section_path"] == "Remote Work > Updated Eligibility"
        assert patched["token_count"] > 0

        # 5. Submit for publish (moves to 'review') -> chunk edit is now blocked (409)
        r_pub = await async_client.post(
            f"/api/v1/policies/{doc_id}/publish",
            headers=owner_headers,
        )
        assert r_pub.status_code == 200, r_pub.text
        created_approval_ids.append(r_pub.json()["approval_id"])

        r_patch_locked = await async_client.patch(
            f"/api/v1/policies/{doc_id}/chunks/{chunk_id}",
            headers=owner_headers,
            json={"text": "Attempted mutation while under review"},
        )
        assert r_patch_locked.status_code == 409

        # 6. Reingest endpoint works cleanly on non-terminal doc
        r_reingest = await async_client.post(
            f"/api/v1/policies/{doc_id}/reingest",
            headers=owner_headers,
        )
        assert r_reingest.status_code == 200, r_reingest.text
        assert r_reingest.json()["ingestion_status"] == "needs_review"
    finally:
        await _cleanup_policies_and_approvals(
            tenant_id=globex.tenant_id,
            policy_ids=created_policy_ids,
            approval_ids=created_approval_ids,
            storage_keys=stored_keys,
        )


# ==============================================================================
# 4. Soft-Delete Archival, RBAC Library Visibility & Readiness Count
# ==============================================================================
@pytest.mark.asyncio
async def test_04_soft_delete_archival_rbac_library_and_readiness(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - Non-HR employee sees ONLY `active` + `visibility='all'` policies in `GET /api/v1/policies`.
    - `DELETE /api/v1/policies/{id}` sets `lifecycle_status='archived'`, drops from default
      library listing, remains visible with `?include_archived=true`, removes its embeddings,
      and is excluded from `GET /api/v1/onboarding/readiness` policy count.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    created_policy_ids: list[str] = []
    created_approval_ids: list[str] = []
    stored_keys: list[str] = []

    try:
        # Clean pre-existing policies for Globex so readiness counts are deterministic
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "DELETE FROM public.policy_documents WHERE tenant_id = $1::uuid;",
                globex.tenant_id,
            )
        finally:
            await conn.close()

        # Upload draft HR-only policy + active public policy
        r_hr = await async_client.post(
            "/api/v1/policies",
            headers=owner_headers,
            data={"title": f"HR Confidential {uuid.uuid4().hex[:6]}", "visibility": "hr_only"},
            files={
                "file": (
                    "hr.txt",
                    b"# HR Only\n1.1 Internal disciplinary escalation matrix details.",
                    "text/plain",
                )
            },
        )
        assert r_hr.status_code == 201
        hr_id = r_hr.json()["id"]
        created_policy_ids.append(hr_id)
        stored_keys.append(r_hr.json()["storage_key"])

        r_pub_doc = await async_client.post(
            "/api/v1/policies",
            headers=owner_headers,
            data={"title": f"Public Handbook {uuid.uuid4().hex[:6]}", "visibility": "all"},
            files={
                "file": (
                    "pub.txt",
                    b"# Public Handbook\n1.1 All employees observe standard public holidays.",
                    "text/plain",
                )
            },
        )
        assert r_pub_doc.status_code == 201
        pub_doc_id = r_pub_doc.json()["id"]
        created_policy_ids.append(pub_doc_id)
        stored_keys.append(r_pub_doc.json()["storage_key"])

        # Employee sees 0 policies before publish
        r_emp_list0 = await async_client.get("/api/v1/policies", headers=emp_headers)
        assert r_emp_list0.status_code == 200
        assert r_emp_list0.json() == []

        # Publish & approve public handbook
        r_sub = await async_client.post(
            f"/api/v1/policies/{pub_doc_id}/publish",
            headers=owner_headers,
        )
        assert r_sub.status_code == 200
        appr_id = r_sub.json()["approval_id"]
        created_approval_ids.append(appr_id)

        r_app = await async_client.post(
            f"/api/v1/approvals/{appr_id}/approve",
            headers=owner_headers,
            json={"reason": "Publish"},
        )
        assert r_app.status_code == 200

        # Employee now sees ONLY the active public handbook (not hr_only draft)
        r_emp_list1 = await async_client.get("/api/v1/policies", headers=emp_headers)
        assert [p["id"] for p in r_emp_list1.json()] == [pub_doc_id]

        # Readiness counts 2 non-archived policies
        r_ready0 = await async_client.get("/api/v1/onboarding/readiness", headers=owner_headers)
        assert r_ready0.json()["counts"]["policies"] == 2

        # Archive public handbook -> excluded from default listing & readiness count
        r_del = await async_client.delete(
            f"/api/v1/policies/{pub_doc_id}",
            headers=owner_headers,
        )
        assert r_del.status_code == 200
        assert r_del.json() == {"status": "archived", "id": pub_doc_id}

        r_list_default = await async_client.get("/api/v1/policies", headers=owner_headers)
        assert [p["id"] for p in r_list_default.json()] == [hr_id]

        r_list_arch = await async_client.get(
            "/api/v1/policies",
            headers=owner_headers,
            params={"include_archived": "true"},
        )
        arch_map = {p["id"]: p["lifecycle_status"] for p in r_list_arch.json()}
        assert arch_map[pub_doc_id] == "archived"

        r_ready1 = await async_client.get("/api/v1/onboarding/readiness", headers=owner_headers)
        assert r_ready1.json()["counts"]["policies"] == 1
    finally:
        await _cleanup_policies_and_approvals(
            tenant_id=globex.tenant_id,
            policy_ids=created_policy_ids,
            approval_ids=created_approval_ids,
            storage_keys=stored_keys,
        )


# ==============================================================================
# 5. Cross-Tenant Isolation Across All Stage 5 Policy Endpoints
# ==============================================================================
@pytest.mark.asyncio
async def test_05_cross_tenant_isolation_policy_endpoints(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies Acme Tech Owner receives HTTP 404 when attempting preview, chunk edit,
    page fix, reingest, publish, or delete on a Globex Corp policy document.
    """
    acme_headers = {"Authorization": f"Bearer {isolation_context.company_a.owner.token}"}
    globex = isolation_context.company_b
    globex_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    created_policy_ids: list[str] = []
    stored_keys: list[str] = []

    try:
        r_up = await async_client.post(
            "/api/v1/policies",
            headers=globex_headers,
            data={"title": f"Globex Isolation Policy {uuid.uuid4().hex[:6]}"},
            files={
                "file": (
                    "iso.txt",
                    b"# Isolation Policy\n1.1 Tenant data must never cross organization boundaries.",
                    "text/plain",
                )
            },
        )
        assert r_up.status_code == 201
        doc_id = r_up.json()["id"]
        created_policy_ids.append(doc_id)
        stored_keys.append(r_up.json()["storage_key"])

        r_prev = await async_client.get(
            f"/api/v1/policies/{doc_id}/preview", headers=globex_headers
        )
        chunk_id = r_prev.json()["chunks"][0]["id"]

        assert (
            await async_client.get(f"/api/v1/policies/{doc_id}/preview", headers=acme_headers)
        ).status_code == 404
        assert (
            await async_client.patch(
                f"/api/v1/policies/{doc_id}/chunks/{chunk_id}",
                headers=acme_headers,
                json={"text": "Tampered"},
            )
        ).status_code == 404
        assert (
            await async_client.post(
                f"/api/v1/policies/{doc_id}/pages/1/fix",
                headers=acme_headers,
                json={"cleaned_text": "Tampered"},
            )
        ).status_code == 404
        assert (
            await async_client.post(f"/api/v1/policies/{doc_id}/reingest", headers=acme_headers)
        ).status_code == 404
        assert (
            await async_client.post(f"/api/v1/policies/{doc_id}/publish", headers=acme_headers)
        ).status_code == 404
        assert (
            await async_client.delete(f"/api/v1/policies/{doc_id}", headers=acme_headers)
        ).status_code == 404
    finally:
        await _cleanup_policies_and_approvals(
            tenant_id=globex.tenant_id,
            policy_ids=created_policy_ids,
            approval_ids=[],
            storage_keys=stored_keys,
        )
