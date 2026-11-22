"""
Sandboxed Temporal Workflows for Monthly Leave Accrual & Year-End Carry-Forward (Stage 6 Prompt 2).

Deterministically discovers active, non-suspended tenants via `list_active_tenant_ids_activity`
and executes idempotent per-tenant accrual / carry-forward activities with bounded retries.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        TenantCarryForwardRequest,
        TenantMonthlyAccrualRequest,
        list_active_tenant_ids_activity,
        run_tenant_carry_forward_activity,
        run_tenant_monthly_accrual_activity,
    )

ACCRUAL_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
)


@dataclass
class LeaveAccrualWorkflowInput:
    """Optional workflow input for deterministic year_month or tenant targeting."""

    year_month: str | None = None
    tenant_ids: list[str] | None = None
    actor_id: str | None = None


@dataclass
class LeaveCarryForwardWorkflowInput:
    """Optional workflow input for deterministic year rollover or tenant targeting."""

    from_year: int | None = None
    to_year: int | None = None
    tenant_ids: list[str] | None = None
    actor_id: str | None = None


@workflow.defn
class LeaveAccrualWorkflow:
    """
    Sandboxed monthly workflow crediting `annual_allowance_days / 12` for `monthly`
    leave types across all active tenants.
    """

    @workflow.run
    async def run(self, inp: LeaveAccrualWorkflowInput | None = None) -> dict[str, Any]:
        effective = inp or LeaveAccrualWorkflowInput()

        if effective.year_month:
            year_month = effective.year_month
        else:
            pkt_today = (workflow.now() + timedelta(hours=5)).date()
            year_month = f"{pkt_today.year:04d}-{pkt_today.month:02d}"

        if effective.tenant_ids is not None:
            tenant_ids = list(effective.tenant_ids)
        else:
            tenant_ids = await workflow.execute_activity(
                list_active_tenant_ids_activity,
                500,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACCRUAL_RETRY_POLICY,
            )

        tenants_processed = 0
        credited_count = 0
        skipped_count = 0
        failed_tenants: list[dict[str, str]] = []

        for tenant_id in tenant_ids:
            try:
                summary = await workflow.execute_activity(
                    run_tenant_monthly_accrual_activity,
                    TenantMonthlyAccrualRequest(
                        tenant_id=tenant_id,
                        year_month=year_month,
                        actor_id=effective.actor_id,
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=ACCRUAL_RETRY_POLICY,
                )
                tenants_processed += 1
                credited_count += int(summary.get("credited_count", 0))
                skipped_count += int(summary.get("skipped_count", 0))
            except Exception as exc:
                workflow.logger.error(
                    f"Monthly leave accrual failed for tenant {tenant_id} ({year_month}): {exc}"
                )
                failed_tenants.append({"tenant_id": tenant_id, "error": str(exc)})

        return {
            "year_month": year_month,
            "tenants_processed": tenants_processed,
            "credited_count": credited_count,
            "skipped_count": skipped_count,
            "failed_tenants": failed_tenants,
        }


@workflow.defn
class LeaveCarryForwardWorkflow:
    """
    Sandboxed annual workflow rolling capped remaining balances from `from_year` into
    `to_year` and initializing `to_year` balances across all active tenants.
    """

    @workflow.run
    async def run(self, inp: LeaveCarryForwardWorkflowInput | None = None) -> dict[str, Any]:
        effective = inp or LeaveCarryForwardWorkflowInput()

        if effective.from_year is not None and effective.to_year is not None:
            from_year = effective.from_year
            to_year = effective.to_year
        else:
            pkt_year = (workflow.now() + timedelta(hours=5)).date().year
            to_year = effective.to_year if effective.to_year is not None else pkt_year
            from_year = effective.from_year if effective.from_year is not None else to_year - 1

        if effective.tenant_ids is not None:
            tenant_ids = list(effective.tenant_ids)
        else:
            tenant_ids = await workflow.execute_activity(
                list_active_tenant_ids_activity,
                500,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=ACCRUAL_RETRY_POLICY,
            )

        tenants_processed = 0
        initialized_count = 0
        carried_count = 0
        skipped_count = 0
        failed_tenants: list[dict[str, str]] = []

        for tenant_id in tenant_ids:
            try:
                summary = await workflow.execute_activity(
                    run_tenant_carry_forward_activity,
                    TenantCarryForwardRequest(
                        tenant_id=tenant_id,
                        from_year=from_year,
                        to_year=to_year,
                        actor_id=effective.actor_id,
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=ACCRUAL_RETRY_POLICY,
                )
                tenants_processed += 1
                initialized_count += int(summary.get("initialized_count", 0))
                carried_count += int(summary.get("carried_count", 0))
                skipped_count += int(summary.get("skipped_count", 0))
            except Exception as exc:
                workflow.logger.error(
                    f"Year-end leave carry-forward failed for tenant {tenant_id} "
                    f"({from_year}->{to_year}): {exc}"
                )
                failed_tenants.append({"tenant_id": tenant_id, "error": str(exc)})

        return {
            "from_year": from_year,
            "to_year": to_year,
            "tenants_processed": tenants_processed,
            "initialized_count": initialized_count,
            "carried_count": carried_count,
            "skipped_count": skipped_count,
            "failed_tenants": failed_tenants,
        }
