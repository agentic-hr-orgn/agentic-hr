"""
CLI Test Runner for Sample Temporal Workflow.
Triggers SampleOnboardingWorkflow on the local Temporal development server and prints execution results.
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from temporalio.client import Client

from app.core.config import settings
from app.workflows import SampleOnboardingWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [workflow-runner] %(message)s",
)
logger = logging.getLogger("agentic_hr.run_workflow")


async def run_sample_workflow(
    candidate_name: str = "Alex Mercer",
    candidate_email: str = "agentic.hr.test.ubaid.candidate1@gmail.com",
) -> dict[str, Any]:
    logger.info(f"Connecting to Temporal development server at {settings.TEMPORAL_ADDRESS}...")
    client = await Client.connect(settings.TEMPORAL_ADDRESS)

    workflow_id = f"sample-onboarding-{uuid.uuid4().hex[:8]}"
    logger.info(
        f"Starting workflow '{workflow_id}' on task queue '{settings.TEMPORAL_TASK_QUEUE}'..."
    )

    handle = await client.start_workflow(
        SampleOnboardingWorkflow.run,
        args=[candidate_name, candidate_email],
        id=workflow_id,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
    )

    logger.info(
        f"Workflow scheduled with Run ID: {handle.result_run_id}. Waiting for completion..."
    )
    result = await handle.result()

    logger.info("----------------------------------------------------------------------")
    logger.info("TEMPORAL WORKFLOW EXECUTION COMPLETED")
    logger.info(f"Workflow ID: {workflow_id}")
    logger.info(f"Result:\n{json.dumps(result, indent=2)}")
    logger.info("----------------------------------------------------------------------")
    return result


def main() -> None:
    asyncio.run(run_sample_workflow())


if __name__ == "__main__":
    main()
