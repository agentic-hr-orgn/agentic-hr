"""
Sample Onboarding Temporal Workflow for Agentic HR Core.
Demonstrates durable orchestration of multi-step activities using fake Google Workspace adapters.
"""

from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        greeting_activity,
        schedule_interview_activity,
        send_welcome_notification,
    )


@workflow.defn(sandboxed=False)
class SampleOnboardingWorkflow:
    """
    Sample candidate onboarding workflow demonstrating:
    1. Greeting activity execution.
    2. Simulated candidate notification (FakeGmailService).
    3. Simulated interview scheduling (FakeCalendarService).
    """

    @workflow.run
    async def run(
        self,
        candidate_name: str = "Alex Mercer",
        candidate_email: str = "agentic.hr.test.ubaid.candidate1@gmail.com",
    ) -> dict[str, Any]:
        workflow.logger.info(
            f"Starting SampleOnboardingWorkflow for candidate: {candidate_name} ({candidate_email})"
        )

        # 1. Step 1: Greeting
        greeting = await workflow.execute_activity(
            greeting_activity,
            candidate_name,
            start_to_close_timeout=timedelta(seconds=10),
        )

        # 2. Step 2: Simulated Email Notification (Fake Gmail)
        email_result = await workflow.execute_activity(
            send_welcome_notification,
            args=[candidate_name, candidate_email],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # 3. Step 3: Simulated Interview Scheduling (Fake Calendar)
        calendar_result = await workflow.execute_activity(
            schedule_interview_activity,
            args=[candidate_name, candidate_email],
            start_to_close_timeout=timedelta(seconds=10),
        )

        workflow.logger.info(
            f"SampleOnboardingWorkflow completed successfully for {candidate_name}"
        )

        return {
            "status": "COMPLETED",
            "workflow": "SampleOnboardingWorkflow",
            "candidate_name": candidate_name,
            "candidate_email": candidate_email,
            "greeting": greeting,
            "email_dispatch": email_result,
            "calendar_event": calendar_result,
            "fake_integrations": True,
        }
