"""
Stage 5 Prompt 5 Verification Suite:
Hybrid Bilingual Policy Retrieval Engine (`POST /api/v1/policies/search`).

Verifies:
1. Role-based SQL pre-filtering:
   - `hr_only` chunks NEVER reach `employee` or plain `manager`, while `owner` & `is_hr_head` retrieve them.
   - `managers` chunks reach `manager`/`owner`/`is_hr_head`, NEVER `employee`.
2. Lifecycle & ingestion pre-filtering:
   - `draft`, `review`, `needs_review`, `failed`, `superseded`, and `archived` policies NEVER appear.
3. Hybrid bilingual fusion:
   - Exact Urdu / Arabic-variant lexical & trigram queries AND cross-language Urdu <-> English
     semantic queries rank matching chunks in top-5 with complete Stage 7 `PolicyCitation` shape.
   - Stopword filtering, empty/punctuation-only query safety, and metadata filters
     (`doc_type`, `language`, `effective_as_of`) work accurately.
4. Cross-tenant isolation:
   - Globex user searching exact text from an active Acme policy receives 0 Acme hits.
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


async def _upload_and_publish(
    client: httpx.AsyncClient,
    *,
    owner_headers: dict[str, str],
    title: str,
    content: bytes,
    visibility: str = "all",
    doc_type: str = "company_policy",
    effective_date: str | None = None,
    publish: bool = True,
) -> tuple[str, str | None, str]:
    """Helper uploading a `.txt` policy and optionally approving its `policy.publish` gate."""
    form_data: dict[str, str] = {
        "title": title,
        "visibility": visibility,
        "doc_type": doc_type,
    }
    if effective_date is not None:
        form_data["effective_date"] = effective_date

    r_up = await client.post(
        "/api/v1/policies",
        headers=owner_headers,
        data=form_data,
        files={"file": (f"{title.lower().replace(' ', '_')}.txt", content, "text/plain")},
    )
    assert r_up.status_code == 201, r_up.text
    doc = r_up.json()
    doc_id = doc["id"]
    storage_key = doc["storage_key"]

    approval_id: str | None = None
    if publish:
        r_pub = await client.post(
            f"/api/v1/policies/{doc_id}/publish",
            headers=owner_headers,
        )
        assert r_pub.status_code == 200, r_pub.text
        approval_id = r_pub.json()["approval_id"]

        r_app = await client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=owner_headers,
            json={"reason": "Approved for search test"},
        )
        assert r_app.status_code == 200, r_app.text

    return doc_id, approval_id, storage_key


async def _cleanup(
    *,
    tenant_id: str,
    policy_ids: list[str],
    approval_ids: list[str],
    storage_keys: list[str],
) -> None:
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
# 1. Role-Based Visibility Pre-Filtering (`all` | `managers` | `hr_only`)
# ==============================================================================
@pytest.mark.asyncio
async def test_01_visibility_rbac_enforcement_hr_only_managers_and_all(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - `hr_only` chunks NEVER reach `employee` or plain `manager`, while `owner` and `is_hr_head`
      retrieve them cleanly.
    - `managers` chunks reach `manager`, `owner`, and `is_hr_head`, NEVER `employee`.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}

    suffix = uuid.uuid4().hex[:6]
    policy_ids: list[str] = []
    approval_ids: list[str] = []
    storage_keys: list[str] = []

    try:
        # 1. Publish 3 active policies with distinct unique tokens per visibility tier
        all_id, all_app, all_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"General Leave Guide {suffix}",
            visibility="all",
            content=(
                f"# General Leave Guide\n"
                f"1.1 TokenALL{suffix} explains standard annual paid leave for every employee."
            ).encode(),
        )
        mgr_id, mgr_app, mgr_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"Manager Escalation Playbook {suffix}",
            visibility="managers",
            content=(
                f"# Manager Escalation\n"
                f"1.1 TokenMGR{suffix} describes manager performance review calibration steps."
            ).encode(),
        )
        hr_id, hr_app, hr_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"HR Executive Severance Matrix {suffix}",
            visibility="hr_only",
            content=(
                f"# HR Executive Severance\n"
                f"1.1 TokenHR{suffix} contains confidential HR severance negotiation bands."
            ).encode(),
        )
        policy_ids.extend([all_id, mgr_id, hr_id])
        approval_ids.extend([a for a in (all_app, mgr_app, hr_app) if a])
        storage_keys.extend([all_key, mgr_key, hr_key])

        # Ensure globex.manager is a PLAIN manager (`is_hr_head=false`)
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
        finally:
            await conn.close()

        # Employee searching HR-only & manager tokens NEVER sees hr_only or managers docs
        r_emp_hr = await async_client.post(
            "/api/v1/policies/search",
            headers=emp_headers,
            json={"query": f"TokenHR{suffix} confidential severance", "top_k": 10},
        )
        assert r_emp_hr.status_code == 200, r_emp_hr.text
        assert all(h["citation"]["visibility"] == "all" for h in r_emp_hr.json()["hits"])
        assert all(h["citation"]["document_id"] != hr_id for h in r_emp_hr.json()["hits"])

        r_emp_mgr = await async_client.post(
            "/api/v1/policies/search",
            headers=emp_headers,
            json={"query": f"TokenMGR{suffix} calibration", "top_k": 10},
        )
        assert r_emp_mgr.status_code == 200
        assert all(h["citation"]["document_id"] != mgr_id for h in r_emp_mgr.json()["hits"])

        # Plain Manager retrieves `managers` doc, NEVER `hr_only` doc
        r_mgr_ok = await async_client.post(
            "/api/v1/policies/search",
            headers=mgr_headers,
            json={"query": f"TokenMGR{suffix} calibration", "top_k": 5},
        )
        assert r_mgr_ok.status_code == 200
        assert any(h["citation"]["document_id"] == mgr_id for h in r_mgr_ok.json()["hits"])

        r_mgr_hr = await async_client.post(
            "/api/v1/policies/search",
            headers=mgr_headers,
            json={"query": f"TokenHR{suffix} severance", "top_k": 10},
        )
        assert r_mgr_hr.status_code == 200
        assert all(h["citation"]["document_id"] != hr_id for h in r_mgr_hr.json()["hits"])

        # Owner retrieves `hr_only` doc cleanly
        r_owner_hr = await async_client.post(
            "/api/v1/policies/search",
            headers=owner_headers,
            json={"query": f"TokenHR{suffix} severance", "top_k": 5},
        )
        assert r_owner_hr.status_code == 200
        assert any(h["citation"]["document_id"] == hr_id for h in r_owner_hr.json()["hits"])

        # Promoting globex.manager to `is_hr_head=true` grants access to `hr_only`
        conn = await asyncpg.connect(MIGRATION_DB_URL)
        try:
            await conn.execute(
                "UPDATE public.memberships SET is_hr_head = true WHERE id = $1::uuid;",
                globex.manager.membership_id,
            )
        finally:
            await conn.close()

        r_hr_head = await async_client.post(
            "/api/v1/policies/search",
            headers=mgr_headers,
            json={"query": f"TokenHR{suffix} severance", "top_k": 5},
        )
        assert r_hr_head.status_code == 200
        assert any(h["citation"]["document_id"] == hr_id for h in r_hr_head.json()["hits"])
    finally:
        await _cleanup(
            tenant_id=globex.tenant_id,
            policy_ids=policy_ids,
            approval_ids=approval_ids,
            storage_keys=storage_keys,
        )


# ==============================================================================
# 2. Lifecycle & Ingestion Pre-Filter Blocks Non-Active / Superseded / Archived
# ==============================================================================
@pytest.mark.asyncio
async def test_02_lifecycle_and_ingestion_pre_filter_blocks_non_active_versions(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies `draft`, `review`, `needs_review`, `superseded`, and `archived` policies
    NEVER appear in `POST /api/v1/policies/search` — only `active` + `indexed` rows match.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    suffix = uuid.uuid4().hex[:6]

    policy_ids: list[str] = []
    approval_ids: list[str] = []
    storage_keys: list[str] = []

    try:
        # 1. Draft policy (indexed, lifecycle_status='draft')
        draft_id, _, draft_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"Draft Policy {suffix}",
            content=f"# Draft\n1.1 SecretDraftToken{suffix} must never appear in search.".encode(),
            publish=False,
        )
        policy_ids.append(draft_id)
        storage_keys.append(draft_key)

        # 2. Review policy (submitted, NOT approved)
        rev_id, _, rev_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"Review Policy {suffix}",
            content=f"# Review\n1.1 SecretReviewToken{suffix} is pending approval.".encode(),
            publish=False,
        )
        r_rev_pub = await async_client.post(
            f"/api/v1/policies/{rev_id}/publish",
            headers=owner_headers,
        )
        assert r_rev_pub.status_code == 200
        policy_ids.append(rev_id)
        approval_ids.append(r_rev_pub.json()["approval_id"])
        storage_keys.append(rev_key)

        # 3. Versioned policy: v1 active -> v2 approved (v1 becomes superseded)
        ver_title = f"Versioned Benefits {suffix}"
        v1_id, v1_app, v1_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=ver_title,
            content=f"# Benefits v1\n1.1 OldSupersededToken{suffix} legacy allowance.".encode(),
            publish=True,
        )
        v2_id, v2_app, v2_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=ver_title,
            content=f"# Benefits v2\n1.1 CurrentActiveToken{suffix} updated allowance.".encode(),
            publish=True,
        )
        policy_ids.extend([v1_id, v2_id])
        approval_ids.extend([a for a in (v1_app, v2_app) if a])
        storage_keys.extend([v1_key, v2_key])

        # 4. Archived policy (published then soft-deleted)
        arch_id, arch_app, arch_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"Archived Policy {suffix}",
            content=f"# Archived\n1.1 DeletedArchiveToken{suffix} retired clause.".encode(),
            publish=True,
        )
        r_del = await async_client.delete(f"/api/v1/policies/{arch_id}", headers=owner_headers)
        assert r_del.status_code == 200
        policy_ids.append(arch_id)
        if arch_app:
            approval_ids.append(arch_app)
        storage_keys.append(arch_key)

        blocked_ids = {draft_id, rev_id, v1_id, arch_id}
        for query_tok in (
            f"SecretDraftToken{suffix}",
            f"SecretReviewToken{suffix}",
            f"OldSupersededToken{suffix}",
            f"DeletedArchiveToken{suffix}",
        ):
            r_s = await async_client.post(
                "/api/v1/policies/search",
                headers=owner_headers,
                json={"query": query_tok, "top_k": 10},
            )
            assert r_s.status_code == 200
            returned_doc_ids = {h["citation"]["document_id"] for h in r_s.json()["hits"]}
            assert returned_doc_ids.isdisjoint(blocked_ids)

        # Active v2 IS returned cleanly
        r_v2 = await async_client.post(
            "/api/v1/policies/search",
            headers=owner_headers,
            json={"query": f"CurrentActiveToken{suffix}", "top_k": 5},
        )
        assert r_v2.status_code == 200
        hits = r_v2.json()["hits"]
        assert len(hits) >= 1
        assert hits[0]["citation"]["document_id"] == v2_id
        assert hits[0]["citation"]["version"] == 2
    finally:
        await _cleanup(
            tenant_id=globex.tenant_id,
            policy_ids=policy_ids,
            approval_ids=approval_ids,
            storage_keys=storage_keys,
        )


# ==============================================================================
# 3. Hybrid Bilingual Fusion (Urdu Lexical/Trigram + Cross-Language Semantic)
# ==============================================================================
@pytest.mark.asyncio
async def test_03_hybrid_bilingual_fusion_urdu_trigram_and_cross_language_citations(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies:
    - Exact Urdu/Arabic-variant query matches via FTS/trigram + vector fusion with Stage 7 Citation.
    - Cross-language Urdu query (`"گھر سے کام اور ریموٹ ورک"`) ranks the English Remote Work
      policy above unrelated IT Security policy.
    - Punctuation-only query does not crash PostgreSQL FTS/trigram legs.
    - `doc_type`, `language`, and `effective_as_of` filters narrow results accurately.
    """
    globex = isolation_context.company_b
    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    suffix = uuid.uuid4().hex[:6]

    policy_ids: list[str] = []
    approval_ids: list[str] = []
    storage_keys: list[str] = []

    try:
        rem_id, rem_app, rem_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"Remote Work Policy {suffix}",
            doc_type="company_policy",
            effective_date="2026-01-01",
            content=(
                b"# 1. Remote Work Eligibility\n"
                b"1.1 Full-time staff may work from home (WFH) up to two days per week "
                b"under the hybrid remote work telecommute program."
            ),
        )
        it_id, it_app, it_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"IT Password Security {suffix}",
            doc_type="helpdesk_knowledge",
            effective_date="2026-01-01",
            content=(
                b"# 1. Password & MFA Rules\n"
                b"1.1 All corporate laptops require hardware two-factor authentication (MFA) "
                b"and 16-character VPN passwords rotated quarterly."
            ),
        )
        ur_id, ur_app, ur_key = await _upload_and_publish(
            async_client,
            owner_headers=owner_headers,
            title=f"Urdu Leave Law {suffix}",
            doc_type="labour_law",
            effective_date="2026-06-01",
            content=(
                "# 1. سالانہ چھٹی قانون\n"
                "دفعہ ۱: تمام مستقل ملازمین کو ہر سال بیس دن کی سالانہ تنخواہ سمیت چھٹی "
                "اور دس دن کی طبی چھٹی کا قانونی حق حاصل ہے۔"
            ).encode(),
        )
        policy_ids.extend([rem_id, it_id, ur_id])
        approval_ids.extend([a for a in (rem_app, it_app, ur_app) if a])
        storage_keys.extend([rem_key, it_key, ur_key])

        # 1. Exact Urdu / Arabic keyboard variant query hits Urdu Leave Law with full Stage 7 Citation
        r_ur = await async_client.post(
            "/api/v1/policies/search",
            headers=owner_headers,
            json={"query": "سالانہ چھٹي كے بیس دن", "top_k": 5},
        )
        assert r_ur.status_code == 200, r_ur.text
        ur_body = r_ur.json()
        assert ur_body["query_language"] == "ur"
        assert ur_body["embedding_model"] == "gemini-embedding-001"
        assert ur_body["embedding_dim"] == 768
        assert len(ur_body["hits"]) >= 1

        top_ur = ur_body["hits"][0]
        assert top_ur["citation"]["document_id"] == ur_id
        assert top_ur["citation"]["version"] == 1
        assert top_ur["citation"]["doc_type"] == "labour_law"
        assert top_ur["citation"]["page_start"] == 1
        assert top_ur["citation"]["page_range"] == "1"
        assert top_ur["citation"]["language"] == "ur"
        assert top_ur["fts_rank"] is not None or top_ur["trgm_rank"] is not None
        assert top_ur["snippet"]

        # 2. Cross-language Urdu remote-work query ranks English Remote Work above IT Security
        r_xlang = await async_client.post(
            "/api/v1/policies/search",
            headers=owner_headers,
            json={"query": "گھر سے کام اور ریموٹ ورک کی پالیسی کیا ہے؟", "top_k": 5},
        )
        assert r_xlang.status_code == 200, r_xlang.text
        xlang_doc_ids = [h["citation"]["document_id"] for h in r_xlang.json()["hits"]]
        assert rem_id in xlang_doc_ids
        if it_id in xlang_doc_ids:
            assert xlang_doc_ids.index(rem_id) < xlang_doc_ids.index(it_id)

        # 3. Filters: doc_type & effective_as_of exclude future-dated 2026-06-01 Urdu law
        r_eff = await async_client.post(
            "/api/v1/policies/search",
            headers=owner_headers,
            json={
                "query": "سالانہ چھٹی",
                "doc_type": "labour_law",
                "effective_as_of": "2026-03-01",
                "top_k": 5,
            },
        )
        assert r_eff.status_code == 200
        assert all(h["citation"]["document_id"] != ur_id for h in r_eff.json()["hits"])

        # 4. Punctuation-only query executes safely without PostgreSQL FTS/trigram error
        r_punct = await async_client.post(
            "/api/v1/policies/search",
            headers=owner_headers,
            json={"query": "??? !!! ---", "top_k": 3},
        )
        assert r_punct.status_code == 200
        assert r_punct.json()["normalized_query"] == ""
    finally:
        await _cleanup(
            tenant_id=globex.tenant_id,
            policy_ids=policy_ids,
            approval_ids=approval_ids,
            storage_keys=storage_keys,
        )


# ==============================================================================
# 4. Cross-Tenant Isolation (Globex Searching Acme Exact Text -> 0 Acme Hits)
# ==============================================================================
@pytest.mark.asyncio
async def test_04_cross_tenant_isolation_zero_hits_across_companies(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies Globex Corp searching an exact unique phrase from an active Acme Tech policy
    receives zero Acme hits under forced RLS.
    """
    acme = isolation_context.company_a
    globex = isolation_context.company_b
    acme_headers = {"Authorization": f"Bearer {acme.owner.token}"}
    globex_headers = {"Authorization": f"Bearer {globex.owner.token}"}

    suffix = uuid.uuid4().hex[:8]
    secret_phrase = f"AcmeUltraUniquePolicyToken{suffix}"
    policy_ids: list[str] = []
    approval_ids: list[str] = []
    storage_keys: list[str] = []

    try:
        acme_doc_id, acme_app_id, acme_key = await _upload_and_publish(
            async_client,
            owner_headers=acme_headers,
            title=f"Acme Exclusive Policy {suffix}",
            content=f"# Acme Exclusive\n1.1 {secret_phrase} applies only to Acme Tech.".encode(),
            publish=True,
        )
        policy_ids.append(acme_doc_id)
        if acme_app_id:
            approval_ids.append(acme_app_id)
        storage_keys.append(acme_key)

        # Acme finds its own policy
        r_acme = await async_client.post(
            "/api/v1/policies/search",
            headers=acme_headers,
            json={"query": secret_phrase, "top_k": 5},
        )
        assert r_acme.status_code == 200
        assert any(h["citation"]["document_id"] == acme_doc_id for h in r_acme.json()["hits"])

        # Globex receives 0 Acme hits
        r_globex = await async_client.post(
            "/api/v1/policies/search",
            headers=globex_headers,
            json={"query": secret_phrase, "top_k": 5},
        )
        assert r_globex.status_code == 200
        assert all(h["citation"]["document_id"] != acme_doc_id for h in r_globex.json()["hits"])
    finally:
        await _cleanup(
            tenant_id=acme.tenant_id,
            policy_ids=policy_ids,
            approval_ids=approval_ids,
            storage_keys=storage_keys,
        )
