"""
FastAPI endpoints for initiating and monitoring Temporal workflows.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from temporalio.client import Client

from app.core.config import settings
from app.workflows import SampleOnboardingWorkflow

router = APIRouter(prefix="/workflows", tags=["Workflows"])


class WorkflowTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_name: str = "Alex Mercer"
    candidate_email: str = "agentic.hr.test.ubaid.candidate1@gmail.com"


class WorkflowTriggerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    workflow_id: str
    result: dict[str, Any]


@router.post(
    "/sample/trigger",
    response_model=WorkflowTriggerResponse,
    summary="Trigger sample onboarding workflow",
    description="Dispatches SampleOnboardingWorkflow to Temporal and returns the execution result.",
)
async def trigger_sample_workflow(payload: WorkflowTriggerRequest) -> WorkflowTriggerResponse:
    workflow_id = f"sample-onboarding-{uuid.uuid4().hex[:8]}"

    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        handle = await client.start_workflow(
            SampleOnboardingWorkflow.run,
            args=[payload.candidate_name, payload.candidate_email],
            id=workflow_id,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        )
        result = await handle.result()
        return WorkflowTriggerResponse(
            status="COMPLETED",
            workflow_id=workflow_id,
            result=result,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to execute Temporal workflow: {exc}",
        ) from exc
