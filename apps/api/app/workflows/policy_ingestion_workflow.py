"""
Sandboxed Temporal Workflow for Durable 4-Step Policy Document Ingestion (Stage 5 Prompt 3).

Strictly sandboxed (`sandboxed=True`) using `with workflow.unsafe.imports_passed_through():`
so Temporal's deterministic workflow runner never imports DB or parser modules inside the
workflow sandbox.
"""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        PolicyIngestionFailRequest,
        PolicyIngestionStepRequest,
        chunk_policy_document_activity,
        embed_policy_chunks_activity,
        finalize_policy_ingestion_activity,
        mark_policy_ingestion_failed_activity,
        parse_policy_document_activity,
    )

INGESTION_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
)


@workflow.defn
class PolicyIngestionWorkflow:
    """
    Sandboxed 4-step policy ingestion workflow:
    1. Parse pages & compute Urdu/OCR quality scores (`progress_pct=30`)
    2. Clause-aware chunking & lexical FTS indexing (`progress_pct=60`)
    3. Idempotent hash-cached 768D L2-normalized embedding (`progress_pct=90`)
    4. Finalize `ingestion_status` (`indexed` or `needs_review`, `progress_pct=100`)
       while preserving `lifecycle_status='draft'`.
    """

    @workflow.run
    async def run(self, tenant_id: str, document_id: str) -> dict[str, Any]:
        step_req = PolicyIngestionStepRequest(
            tenant_id=tenant_id,
            document_id=document_id,
        )
        try:
            step1 = await workflow.execute_activity(
                parse_policy_document_activity,
                step_req,
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=INGESTION_RETRY_POLICY,
            )
            step2 = await workflow.execute_activity(
                chunk_policy_document_activity,
                step_req,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=INGESTION_RETRY_POLICY,
            )
            step3 = await workflow.execute_activity(
                embed_policy_chunks_activity,
                step_req,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=INGESTION_RETRY_POLICY,
            )
            step4 = await workflow.execute_activity(
                finalize_policy_ingestion_activity,
                step_req,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=INGESTION_RETRY_POLICY,
            )
            return {
                **step4,
                "page_count": step1.get("page_count", 0),
                "chunk_count": step2.get("chunk_count", 0),
                "already_embedded": step3.get("already_embedded", 0),
                "reused_from_cache": step3.get("reused_from_cache", 0),
                "newly_embedded": step3.get("newly_embedded", 0),
            }
        except Exception as exc:
            workflow.logger.error(
                f"PolicyIngestionWorkflow failed for tenant={tenant_id}, doc={document_id}: {exc}"
            )
            failed_result = await workflow.execute_activity(
                mark_policy_ingestion_failed_activity,
                PolicyIngestionFailRequest(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    error_message=str(exc),
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            return failed_result
