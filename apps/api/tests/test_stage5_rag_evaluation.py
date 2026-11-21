"""
Stage 5 Prompt 7 — End-to-End Bilingual RAG Evaluation & Security Exit Gate Suite.

Executes the full Stage 5 pipeline on the 6-policy (7-document) synthetic corpus:
1. Uploads all 7 `.docx`/`.pdf` fixtures via `POST /api/v1/policies`.
2. Verifies the partly scanned `IT & Acceptable Usage Policy` enters `needs_review` in
   `/api/v1/policies/review-queue`, repairs Page 2 via `POST /api/v1/policies/{id}/pages/2/fix`,
   and confirms it transitions to `indexed`.
3. Submits & approves all policies through Stage 2 `policy.publish` governance so Leave Policy v2
   atomically supersedes Leave Policy v1.
4. Evaluates the 18-question gold set (`apps/api/tests/fixtures/stage5_gold_set.json`) via
   `POST /api/v1/policies/search` (`top_k=5`) and asserts all 5 Stage 5 Exit Gates:
   - Overall Recall@5 >= 90% and Cross-Language Recall@5 >= 90%.
   - 100% Visibility Gate (`employee` and plain `manager` get 0 `hr_only` chunks).
   - 100% Superseded Gate (0 chunks from Leave Policy v1 across all roles).
   - 100% Prompt-Injection Gate (`Code of Conduct` injection chunk carries `has_injection_flag=True`
     without leaking restricted data).
   - 100% Cross-Tenant Gate (other tenant receives 0 chunks for exact policy phrases).
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio

from app.services.storage_helper import StorageHelper
from tests.fixtures.stage5_policies.builder import (
    CORPUS_SPECS,
    IT_USAGE_PAGE_2_REPAIRED_TEXT,
    PolicyFixtureSpec,
    build_all_stage5_policy_files,
)
from tests.isolation_harness import MIGRATION_DB_URL, MultiTenantTestContext

GOLD_SET_PATH = Path(__file__).resolve().parent / "fixtures" / "stage5_gold_set.json"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "stage5_policies"


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


async def _upload_fixture(
    async_client: httpx.AsyncClient,
    *,
    owner_headers: dict[str, str],
    spec: PolicyFixtureSpec,
    file_path: Path,
) -> dict[str, object]:
    form_data: dict[str, str] = {
        "title": spec.title,
        "doc_type": spec.doc_type,
        "visibility": spec.visibility,
        "language": spec.language,
        "source": spec.source,
    }
    if spec.effective_date:
        form_data["effective_date"] = spec.effective_date

    resp = await async_client.post(
        "/api/v1/policies",
        headers=owner_headers,
        data=form_data,
        files={"file": (spec.file_name, file_path.read_bytes(), spec.mime_type)},
    )
    assert resp.status_code == 201, f"Failed uploading {spec.file_name}: {resp.text}"
    return resp.json()


async def _publish_and_approve(
    async_client: httpx.AsyncClient,
    *,
    owner_headers: dict[str, str],
    policy_id: str,
    reason: str,
) -> str:
    r_pub = await async_client.post(
        f"/api/v1/policies/{policy_id}/publish",
        headers=owner_headers,
    )
    assert r_pub.status_code == 200, f"Publish submit failed for {policy_id}: {r_pub.text}"
    approval_id = str(r_pub.json()["approval_id"])

    r_app = await async_client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers=owner_headers,
        json={"reason": reason},
    )
    assert r_app.status_code == 200, f"Publish approve failed for {approval_id}: {r_app.text}"
    return approval_id


async def _cleanup_eval_corpus(
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


def _hit_matches_gold(hit: dict[str, object], item: dict[str, object]) -> bool:
    citation = hit["citation"]
    assert isinstance(citation, dict)
    if citation.get("title") != item["expected_title"]:
        return False
    if int(citation.get("version", 0)) != int(item["expected_version"]):
        return False
    haystack = f"{hit.get('text', '')} {citation.get('section_path', '')}".lower()
    expected_keywords = [str(kw).lower() for kw in item["expected_keywords"]]
    return any(kw in haystack for kw in expected_keywords)


@pytest.mark.asyncio
async def test_stage5_corpus_gold_set_recall_and_security_exit_gates(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Runs the full Stage 5 Prompt 7 evaluation across all 7 synthetic policy documents and
    18 gold-set questions, enforcing all 5 Exit Gate invariants.
    """
    globex = isolation_context.company_b
    acme = isolation_context.company_a

    owner_headers = {"Authorization": f"Bearer {globex.owner.token}"}
    mgr_headers = {"Authorization": f"Bearer {globex.manager.token}"}
    emp_headers = {"Authorization": f"Bearer {globex.employee.token}"}
    acme_owner_headers = {"Authorization": f"Bearer {acme.owner.token}"}

    # Ensure globex.manager is a PLAIN manager (`is_hr_head=false`) for visibility checks
    conn = await asyncpg.connect(MIGRATION_DB_URL)
    try:
        await conn.execute(
            "UPDATE public.memberships SET is_hr_head = false WHERE id = $1::uuid;",
            globex.manager.membership_id,
        )
    finally:
        await conn.close()

    fixture_paths = build_all_stage5_policy_files(FIXTURE_DIR)
    gold_items: list[dict[str, object]] = json.loads(GOLD_SET_PATH.read_text(encoding="utf-8"))
    assert len(gold_items) == 18

    policy_ids: list[str] = []
    approval_ids: list[str] = []
    storage_keys: list[str] = []
    uploaded_by_key: dict[str, dict[str, object]] = {}

    try:
        # 1. Upload all 7 documents (including Leave v1 -> v2 & partly scanned IT Usage)
        for spec in CORPUS_SPECS:
            doc_json = await _upload_fixture(
                async_client,
                owner_headers=owner_headers,
                spec=spec,
                file_path=fixture_paths[spec.key],
            )
            uploaded_by_key[spec.key] = doc_json
            policy_ids.append(str(doc_json["id"]))
            storage_keys.append(str(doc_json["storage_key"]))
            assert int(doc_json["version"]) == spec.expected_version

        # 2. Verify IT & Acceptable Usage Policy Page 2 triggered needs_review & review-queue
        it_doc = uploaded_by_key["it_usage"]
        it_id = str(it_doc["id"])
        assert it_doc["ingestion_status"] == "needs_review"

        r_queue = await async_client.get("/api/v1/policies/review-queue", headers=owner_headers)
        assert r_queue.status_code == 200, r_queue.text
        queue_ids = {item["document"]["id"] for item in r_queue.json()}
        assert it_id in queue_ids

        # Publish must fail while IT policy is still needs_review
        r_blocked_pub = await async_client.post(
            f"/api/v1/policies/{it_id}/publish",
            headers=owner_headers,
        )
        assert r_blocked_pub.status_code in (400, 409, 422), r_blocked_pub.text

        # Repair Page 2 via fix endpoint -> transitions to indexed
        r_fix = await async_client.post(
            f"/api/v1/policies/{it_id}/pages/2/fix",
            headers=owner_headers,
            json={"cleaned_text": IT_USAGE_PAGE_2_REPAIRED_TEXT},
        )
        assert r_fix.status_code == 200, r_fix.text
        assert r_fix.json()["ingestion_status"] == "indexed"

        # 3. Verify Code of Conduct flagged embedded prompt-injection text
        coc_doc = uploaded_by_key["code_of_conduct"]
        assert coc_doc["has_prompt_injection_warning"] is True

        # 4. Publish & Approve Leave v1 first, then Leave v2 (supersedes v1), plus remaining 5
        publish_order = [
            "leave_v1",
            "leave_v2",
            "attendance",
            "code_of_conduct",
            "it_usage",
            "benefits_bilingual",
            "salary_bands",
        ]
        for key in publish_order:
            pid = str(uploaded_by_key[key]["id"])
            app_id = await _publish_and_approve(
                async_client,
                owner_headers=owner_headers,
                policy_id=pid,
                reason=f"Stage 5 corpus publish approval ({key})",
            )
            approval_ids.append(app_id)

        # Confirm Leave v1 is now superseded and Leave v2 is active
        r_v1 = await async_client.get(
            f"/api/v1/policies/{uploaded_by_key['leave_v1']['id']}/preview",
            headers=owner_headers,
        )
        assert r_v1.status_code == 200
        assert r_v1.json()["document"]["lifecycle_status"] == "superseded"

        r_v2 = await async_client.get(
            f"/api/v1/policies/{uploaded_by_key['leave_v2']['id']}/preview",
            headers=owner_headers,
        )
        assert r_v2.status_code == 200
        assert r_v2.json()["document"]["lifecycle_status"] == "active"

        # 5. Evaluate all 18 Gold Set questions (`top_k=5`)
        total_hits = 0
        cross_total = 0
        cross_hits = 0
        report_lines: list[str] = []

        for item in gold_items:
            actor_role = str(item["actor_role"])
            headers = owner_headers if actor_role == "owner" else emp_headers
            r_search = await async_client.post(
                "/api/v1/policies/search",
                headers=headers,
                json={"query": item["query"], "top_k": 5},
            )
            assert r_search.status_code == 200, f"{item['id']} failed: {r_search.text}"
            payload = r_search.json()
            hits = payload["hits"]

            # Validate Stage 7 citation shape and zero v1 leakage
            for hit in hits:
                cit = hit["citation"]
                for required_field in (
                    "chunk_id",
                    "document_id",
                    "title",
                    "version",
                    "doc_type",
                    "visibility",
                    "section_path",
                    "page_start",
                    "page_end",
                    "page_range",
                    "language",
                ):
                    assert required_field in cit and cit[required_field] is not None
                assert not (
                    cit["title"] == "Leave & Time-Off Policy" and int(cit["version"]) == 1
                ), f"Superseded Leave Policy v1 leaked in {item['id']}!"

            matched_rank = next(
                (idx for idx, h in enumerate(hits, start=1) if _hit_matches_gold(h, item)),
                None,
            )
            is_hit = matched_rank is not None
            if is_hit:
                total_hits += 1
            if item["category"] == "cross_language":
                cross_total += 1
                if is_hit:
                    cross_hits += 1

            top_cit = hits[0]["citation"] if hits else {}
            report_lines.append(
                f"{item['id']} [{item['category']}/{item['query_language']}]: "
                f"{'HIT@' + str(matched_rank) if is_hit else 'MISS'} "
                f"(top={top_cit.get('title')} v{top_cit.get('version')})"
            )

        recall_at_5 = total_hits / float(len(gold_items))
        cross_recall_at_5 = cross_hits / float(cross_total)
        print("\n=== Stage 5 18-Question Gold Set Evaluation ===")
        for line in report_lines:
            print(" ", line)
        print(
            f"Overall Recall@5: {total_hits}/{len(gold_items)} = {recall_at_5:.1%} | "
            f"Cross-Language Recall@5: {cross_hits}/{cross_total} = {cross_recall_at_5:.1%}"
        )

        # Exit Gate 1: Recall@5 >= 90%
        assert recall_at_5 >= 0.90, f"Recall@5 {recall_at_5:.1%} < 90%:\n" + "\n".join(report_lines)
        assert cross_recall_at_5 >= 0.90, f"Cross-lang Recall@5 {cross_recall_at_5:.1%} < 90%"

        # Exit Gate 2: Visibility Gate (`employee` & plain `manager` get 0 `hr_only` chunks)
        for role_label, headers in (("employee", emp_headers), ("manager", mgr_headers)):
            for q in (
                "PKR salary band L5 Engineering Manager executive bonus multiplier",
                "annual salary increment matrix July Exceeds Expectations",
            ):
                r_vis = await async_client.post(
                    "/api/v1/policies/search",
                    headers=headers,
                    json={"query": q, "top_k": 10},
                )
                assert r_vis.status_code == 200
                for hit in r_vis.json()["hits"]:
                    assert hit["citation"]["visibility"] != "hr_only", (
                        f"HR-only chunk leaked to {role_label} on query {q!r}"
                    )

        # Exit Gate 3: Superseded Gate (0 chunks from Leave Policy v1 across all roles)
        for headers in (emp_headers, mgr_headers, owner_headers):
            r_sup = await async_client.post(
                "/api/v1/policies/search",
                headers=headers,
                json={"query": "legacy 14 days annual leave 5 carry-forward", "top_k": 10},
            )
            assert r_sup.status_code == 200
            for hit in r_sup.json()["hits"]:
                cit = hit["citation"]
                assert not (cit["title"] == "Leave & Time-Off Policy" and int(cit["version"]) == 1)

        # Exit Gate 4: Prompt-Injection Gate (`has_injection_flag=True` preserved safely)
        r_inj = await async_client.post(
            "/api/v1/policies/search",
            headers=emp_headers,
            json={"query": "IGNORE ALL PREVIOUS RULES CONFIDENTIALITY CNIC", "top_k": 5},
        )
        assert r_inj.status_code == 200
        inj_hits = r_inj.json()["hits"]
        assert any(
            h["citation"]["title"] == "Code of Conduct & Workplace Ethics"
            and h["has_injection_flag"] is True
            for h in inj_hits
        )
        for h in inj_hits:
            assert h["citation"]["visibility"] != "hr_only"

        # Exit Gate 5: Cross-Tenant Gate (Acme user searching exact Globex phrases gets 0 hits)
        for cross_q in (
            "Acme Tech Solutions 20 paid annual leave days",
            "BitLocker FileVault security@acmetech.internal",
            "L5 Engineering Manager 800,000 - 1,200,000",
        ):
            r_cross = await async_client.post(
                "/api/v1/policies/search",
                headers=acme_owner_headers,
                json={"query": cross_q, "top_k": 10},
            )
            assert r_cross.status_code == 200
            for hit in r_cross.json()["hits"]:
                assert str(hit["citation"]["document_id"]) not in set(policy_ids)

    finally:
        await _cleanup_eval_corpus(
            tenant_id=globex.tenant_id,
            policy_ids=policy_ids,
            approval_ids=approval_ids,
            storage_keys=storage_keys,
        )
