"""
Temporal Background Worker Entrypoint for Agentic HR Core.
Orchestrates durable background workflows (DocuVerify, Screener, Sheets sync, approval gates).
Connects to Temporal Server and listens on configured task queue.
"""

import asyncio
import logging
import signal
import sys

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.workflows import (
    SampleOnboardingWorkflow,
    greeting_activity,
    schedule_interview_activity,
    send_welcome_notification,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [temporal-worker] %(message)s",
)
logger = logging.getLogger("agentic_hr.worker")


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

    worker = Worker(
        client,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        workflows=[SampleOnboardingWorkflow],
        activities=[
            greeting_activity,
            send_welcome_notification,
            schedule_interview_activity,
        ],
    )

    logger.info(f"Worker registered for task queue '{settings.TEMPORAL_TASK_QUEUE}'.")
    logger.info("Registered Workflows: SampleOnboardingWorkflow")
    logger.info(
        "Registered Activities: greeting_activity, send_welcome_notification, schedule_interview_activity"
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
        worker_task = asyncio.create_task(worker.run())
        await stop_event.wait()
        logger.info("Stopping worker task...")
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Temporal worker shut down cleanly.")


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker exited via keyboard interrupt.")
        sys.exit(0)


if __name__ == "__main__":
    main()
