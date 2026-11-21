"""
Temporal Background Worker Entrypoint for Agentic HR Core.
Orchestrates durable background workflows (DocuVerify, Screener, Sheets sync, approval gates,
and daily 09:00 Asia/Karachi document expiry scanning).
Connects to Temporal Server and listens on configured task queue.
"""

import asyncio
import logging
import signal
import sys

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.worker import Worker

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.outbox_publisher import OutboxPublisherWorker
from app.workflows import (
    DocumentExpiryScanInput,
    DocumentExpiryScanWorkflow,
    DummyApprovalWorkflow,
    HumanApprovalWorkflow,
    PolicyIngestionWorkflow,
    SampleOnboardingWorkflow,
    WaitForApprovalWorkflow,
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [temporal-worker] %(message)s",
)
logger = logging.getLogger("agentic_hr.worker")

DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID = "daily-document-expiry-scan"
DAILY_DOCUMENT_EXPIRY_CRON = "0 9 * * *"
DAILY_DOCUMENT_EXPIRY_TZ = "Asia/Karachi"


def build_daily_document_expiry_schedule() -> Schedule:
    """Builds the canonical Temporal Schedule definition (09:00 Asia/Karachi / 04:00 UTC)."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            DocumentExpiryScanWorkflow.run,
            DocumentExpiryScanInput(),
            id="document-expiry-scan",
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        ),
        spec=ScheduleSpec(
            cron_expressions=[DAILY_DOCUMENT_EXPIRY_CRON],
            time_zone_name=DAILY_DOCUMENT_EXPIRY_TZ,
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_daily_document_expiry_schedule(client: Client) -> str:
    """
    Idempotently creates or updates the daily `daily-document-expiry-scan` Temporal Schedule
    at 09:00 Asia/Karachi (04:00 UTC).
    """
    desired_schedule = build_daily_document_expiry_schedule()
    try:
        await client.create_schedule(DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID, desired_schedule)
        logger.info(
            "Created Temporal Schedule '%s' (%s %s).",
            DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID,
            DAILY_DOCUMENT_EXPIRY_CRON,
            DAILY_DOCUMENT_EXPIRY_TZ,
        )
        return "created"
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID)

        async def _updater(_input: ScheduleUpdateInput) -> ScheduleUpdate:
            return ScheduleUpdate(schedule=desired_schedule)

        await handle.update(_updater)
        logger.info(
            "Updated existing Temporal Schedule '%s' (%s %s).",
            DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID,
            DAILY_DOCUMENT_EXPIRY_CRON,
            DAILY_DOCUMENT_EXPIRY_TZ,
        )
        return "updated"


async def run_worker() -> None:
    logger.info("Initializing Agentic HR Background Worker...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Temporal Host: {settings.TEMPORAL_ADDRESS}")
    logger.info(f"Task Queue: {settings.TEMPORAL_TASK_QUEUE}")
    logger.info(f"Fake Integrations Mode: {settings.USE_FAKE_INTEGRATIONS}")

    try:
        logger.info(f"Connecting to Temporal server at {settings.TEMPORAL_ADDRESS}...")
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        logger.info("Successfully connected to Temporal server.")
    except Exception as exc:
        logger.error(
            f"Failed to connect to Temporal server at {settings.TEMPORAL_ADDRESS}: {exc}\n"
            "Ensure the Temporal container is running: 'docker-compose up -d temporal'"
        )
        sys.exit(1)

    try:
        await ensure_daily_document_expiry_schedule(client)
    except Exception as exc:
        logger.warning(
            "Could not register Temporal Schedule '%s' at startup (continuing worker): %s",
            DAILY_DOCUMENT_EXPIRY_SCHEDULE_ID,
            exc,
        )

    worker = Worker(
        client,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        workflows=[
            SampleOnboardingWorkflow,
            HumanApprovalWorkflow,
            DummyApprovalWorkflow,
            WaitForApprovalWorkflow,
            DocumentExpiryScanWorkflow,
            PolicyIngestionWorkflow,
        ],
        activities=[
            greeting_activity,
            send_welcome_notification,
            schedule_interview_activity,
            notify_changes_requested_activity,
            notify_approval_decision_activity,
            notify_resubmitted_activity,
            verify_approval_record_activity,
            get_approval_decision_activity,
            execute_dummy_approved_action_activity,
            execute_dummy_rejected_action_activity,
            send_tenant_email_activity,
            list_active_tenant_ids_activity,
            scan_tenant_expiring_documents_activity,
            parse_policy_document_activity,
            chunk_policy_document_activity,
            embed_policy_chunks_activity,
            finalize_policy_ingestion_activity,
            mark_policy_ingestion_failed_activity,
        ],
    )

    outbox_worker = OutboxPublisherWorker(session_factory=AsyncSessionLocal)

    logger.info(f"Worker registered for task queue '{settings.TEMPORAL_TASK_QUEUE}'.")
    logger.info(
        "Registered Workflows: SampleOnboardingWorkflow, HumanApprovalWorkflow, "
        "DummyApprovalWorkflow, WaitForApprovalWorkflow, DocumentExpiryScanWorkflow"
    )

    stop_event = asyncio.Event()

    def handle_signal(*_args: object) -> None:
        logger.info("Termination signal received. Shutting down worker gracefully...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            signal.signal(sig, handle_signal)

    logger.info("Worker started and listening for workflow executions.")

    try:
        await outbox_worker.start()
        worker_task = asyncio.create_task(worker.run())
        await stop_event.wait()
        logger.info("Stopping worker task...")
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    finally:
        await outbox_worker.stop()
        logger.info("Temporal and Outbox workers shut down cleanly.")


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker exited via keyboard interrupt.")
        sys.exit(0)


if __name__ == "__main__":
    main()
