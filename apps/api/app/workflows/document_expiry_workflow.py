"""
Sandboxed Daily Temporal Workflow for Employee Document Expiry Scanning (Stage 4 Prompt 5).

Deterministically discovers all active, non-suspended tenants via
`list_active_tenant_ids_activity` and scans each tenant for 30, 7, and 1-day document
expirations via `scan_tenant_expiring_documents_activity` with bounded RetryPolicy.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        TenantExpiryScanRequest,
        list_active_tenant_ids_activity,
        scan_tenant_expiring_documents_activity,
    )

SCAN_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
)


@dataclass
class DocumentExpiryScanInput:
    """Optional workflow input allowing deterministic reference date or tenant targeting."""

    reference_date_iso: str | None = None
    tenant_ids: list[str] | None = None
    send_emails: bool = True


@workflow.defn
class DocumentExpiryScanWorkflow:
    """
    Sandboxed (deterministic) daily workflow scanning active tenants for expiring
    employee documents at 30, 7, and 1-day thresholds in Asia/Karachi (UTC+05:00).
    """

    @workflow.run
    async def run(self, inp: DocumentExpiryScanInput | None = None) -> dict[str, Any]:
        effective_input = inp or DocumentExpiryScanInput()

        # Deterministic Asia/Karachi (UTC+05:00, no DST) date derived from workflow.now()
        if effective_input.reference_date_iso:
            ref_date_iso = effective_input.reference_date_iso
        else:
            ref_date_iso = (workflow.now() + timedelta(hours=5)).date().isoformat()

        if effective_input.tenant_ids is not None:
            tenant_ids = list(effective_input.tenant_ids)
        else:
            tenant_ids = await workflow.execute_activity(
                list_active_tenant_ids_activity,
                500,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=SCAN_RETRY_POLICY,
            )

        tenants_scanned = 0
        alerts_created = 0
        alerts_deduplicated = 0
        emails_sent = 0
        emails_skipped = 0
        failed_tenants: list[dict[str, str]] = []

        for tenant_id in tenant_ids:
            try:
                summary = await workflow.execute_activity(
                    scan_tenant_expiring_documents_activity,
                    TenantExpiryScanRequest(
                        tenant_id=tenant_id,
                        reference_date_iso=ref_date_iso,
                        send_emails=effective_input.send_emails,
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=SCAN_RETRY_POLICY,
                )
                tenants_scanned += 1
                alerts_created += int(summary.get("alerts_created", 0))
                alerts_deduplicated += int(summary.get("alerts_deduplicated", 0))
                emails_sent += int(summary.get("emails_sent", 0))
                emails_skipped += int(summary.get("emails_skipped", 0))
            except Exception as exc:
                workflow.logger.error(f"Document expiry scan failed for tenant {tenant_id}: {exc}")
                failed_tenants.append({"tenant_id": tenant_id, "error": str(exc)})

        return {
            "reference_date": ref_date_iso,
            "tenants_scanned": tenants_scanned,
            "alerts_created": alerts_created,
            "alerts_deduplicated": alerts_deduplicated,
            "emails_sent": emails_sent,
            "emails_skipped": emails_skipped,
            "failed_tenants": failed_tenants,
        }
