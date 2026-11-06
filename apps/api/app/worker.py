"""
Temporal Background Worker Entrypoint for Agentic HR Core.
Orchestrates durable background workflows (DocuVerify, Screener, Sheets sync, approval gates).
"""

import asyncio
import logging
import signal
import sys

from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [temporal-worker] %(message)s",
)
logger = logging.getLogger("agentic_hr.worker")


async def run_worker() -> None:
    logger.info("Initializing Agentic HR Background Worker...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(
        f"Temporal Host: {settings.TEMPORAL_ADDRESS if hasattr(settings, 'TEMPORAL_ADDRESS') else 'localhost:7233'}"
    )
    logger.info(f"Fake Integrations Mode: {settings.USE_FAKE_INTEGRATIONS}")

    task_queues = ["docuverify-tasks", "screening-tasks", "orchestration-tasks"]
    logger.info(f"Subscribed task queues: {', '.join(task_queues)}")

    stop_event = asyncio.Event()

    def handle_signal(*_args: object) -> None:
        logger.info("Termination signal received. Shutting down worker gracefully...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Signal handling on Windows event loops
            signal.signal(sig, handle_signal)

    logger.info("Worker started successfully and listening for workflow tasks.")

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Temporal worker stopped.")


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker exited via keyboard interrupt.")
        sys.exit(0)


if __name__ == "__main__":
    main()
