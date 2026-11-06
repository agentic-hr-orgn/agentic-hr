"""
Automated Tests for Temporal Activities and Sample Onboarding Workflow.
Verifies activity logic, fake integration boundaries, and end-to-end workflow execution.
"""

import pytest
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from app.workflows import (
    SampleOnboardingWorkflow,
    greeting_activity,
    schedule_interview_activity,
    send_welcome_notification,
)


@pytest.mark.asyncio
async def test_greeting_activity_direct() -> None:
    """Verifies that greeting_activity produces the expected formatted message."""
    env = ActivityEnvironment()
    result = await env.run(greeting_activity, "Taylor Swift")
    assert result == "Hello from Agentic HR Temporal Workflow, Taylor Swift!"


@pytest.mark.asyncio
async def test_send_welcome_notification_activity_fake() -> None:
    """
    Verifies that send_welcome_notification routes strictly through FakeGmailService,
    producing a simulated response without making real API calls.
    """
    env = ActivityEnvironment()
    candidate_email = "agentic.hr.test.ubaid.candidate1@gmail.com"
    result = await env.run(send_welcome_notification, "Jane Doe", candidate_email)

    assert result["status"] == "success"
    assert result["fake"] is True
    assert result["message_id"].startswith("fake_msg_")
    assert result["details"]["to"] == candidate_email
    assert "agentic.hr.test.ubaid" in result["details"]["sender"]


@pytest.mark.asyncio
async def test_schedule_interview_activity_fake() -> None:
    """
    Verifies that schedule_interview_activity routes strictly through FakeCalendarService,
    producing a simulated Google Meet event without making real API calls.
    """
    env = ActivityEnvironment()
    candidate_email = "agentic.hr.test.ubaid.candidate2@gmail.com"
    result = await env.run(schedule_interview_activity, "John Smith", candidate_email)

    assert result["status"] == "confirmed"
    assert result["fake"] is True
    assert result["event_id"].startswith("fake_event_")
    assert "https://meet.google.com/fake-" in result["details"]["hangout_link"]
    assert candidate_email in result["details"]["attendees"]


@pytest.mark.asyncio
async def test_sample_onboarding_workflow_e2e() -> None:
    """
    Executes SampleOnboardingWorkflow in an isolated Temporal test environment,
    verifying end-to-end orchestration of all three activities.
    """
    env = await WorkflowEnvironment.start_time_skipping()
    async with (
        env,
        Worker(
            env.client,
            task_queue="test-onboarding-queue",
            workflows=[SampleOnboardingWorkflow],
            activities=[
                greeting_activity,
                send_welcome_notification,
                schedule_interview_activity,
            ],
        ),
    ):
        candidate_name = "Alex Mercer"
        candidate_email = "agentic.hr.test.ubaid.candidate1@gmail.com"

        result = await env.client.execute_workflow(
            SampleOnboardingWorkflow.run,
            args=[candidate_name, candidate_email],
            id="test-sample-onboarding-workflow",
            task_queue="test-onboarding-queue",
        )

        assert result["status"] == "COMPLETED"
        assert result["workflow"] == "SampleOnboardingWorkflow"
        assert result["candidate_name"] == candidate_name
        assert result["candidate_email"] == candidate_email
        assert result["fake_integrations"] is True
        assert "Alex Mercer" in result["greeting"]

        # Verify fake email dispatch
        assert result["email_dispatch"]["fake"] is True
        assert result["email_dispatch"]["message_id"].startswith("fake_msg_")

        # Verify fake calendar event
        assert result["calendar_event"]["fake"] is True
        assert (
            "https://meet.google.com/fake-" in result["calendar_event"]["details"]["hangout_link"]
        )
