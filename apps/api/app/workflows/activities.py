"""
Temporal Activities for Agentic HR Core.
Encapsulates durable execution tasks using fake integration adapters (Gmail, Calendar, Drive, Sheets).
Enforces ADR 006: strictly zero real external API calls under USE_FAKE_INTEGRATIONS=true.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import activity

from app.integrations.base import CalendarEvent, EmailMessage
from app.integrations.factory import get_calendar_service, get_email_service


@activity.defn
async def greeting_activity(name: str) -> str:
    """Simple activity returning a formatted workflow greeting."""
    activity.logger.info(f"Executing greeting_activity for: {name}")
    return f"Hello from Agentic HR Temporal Workflow, {name}!"


@activity.defn
async def send_welcome_notification(candidate_name: str, candidate_email: str) -> dict[str, Any]:
    """
    Sends an automated onboarding / candidate acknowledgement notification.
    Routes strictly through FakeGmailService (simulated dispatch).
    """
    activity.logger.info(f"Executing send_welcome_notification to {candidate_email}")
    email_service = get_email_service()

    message = EmailMessage(
        to_email=candidate_email,
        subject="Welcome to Agentic HR Platform - Application Received",
        body_text=(
            f"Dear {candidate_name},\n\n"
            "Thank you for submitting your profile. Your application has been ingested "
            "into our autonomous recruiting workflow (DocuVerify & Screener).\n\n"
            "Best regards,\nRecruitment Team\nAcme Tech Solutions"
        ),
        sender_email="agentic.hr.test.ubaid.recruiter@gmail.com",
    )

    result = await email_service.send_email(message)
    activity.logger.info(
        f"Email notification result: {result.get('status')} (fake={result.get('fake')})"
    )
    return result


@activity.defn
async def schedule_interview_activity(candidate_name: str, candidate_email: str) -> dict[str, Any]:
    """
    Schedules a technical assessment / screening interview.
    Routes strictly through FakeCalendarService (simulated Google Meet event).
    """
    activity.logger.info(f"Executing schedule_interview_activity for {candidate_name}")
    calendar_service = get_calendar_service()

    start_time = datetime.now(UTC) + timedelta(days=2)
    end_time = start_time + timedelta(minutes=45)

    event = CalendarEvent(
        title=f"Technical Assessment Interview - {candidate_name}",
        start_time=start_time,
        end_time=end_time,
        attendees=[
            candidate_email,
            "agentic.hr.test.ubaid.hm@gmail.com",
            "agentic.hr.test.ubaid.recruiter@gmail.com",
        ],
        description="Autonomous initial candidate screening and rubric evaluation discussion.",
        location="Google Meet",
    )

    result = await calendar_service.create_event(event)
    activity.logger.info(
        f"Calendar event result: {result.get('status')} (meet={result.get('details', {}).get('hangout_link')})"
    )
    return result
