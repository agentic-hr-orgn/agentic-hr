import uuid
from typing import Any

from app.integrations.base import EmailMessage, EmailService


class FakeGmailService(EmailService):
    """
    Fake Gmail service that records sent messages in memory.
    Enforces ADR 006: Stage 1 USE_FAKE_INTEGRATIONS=true, no real emails sent.
    """

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []

    async def send_email(self, message: EmailMessage) -> dict[str, Any]:
        msg_id = f"fake_msg_{uuid.uuid4().hex[:8]}"
        record = {
            "id": msg_id,
            "status": "SENT_FAKE",
            "to": message.to_email,
            "subject": message.subject,
            "body_text": message.body_text,
            "sender": message.sender_email or "agentic.hr.test.ubaid.owner@gmail.com",
        }
        self.sent_messages.append(record)
        return {
            "message_id": msg_id,
            "status": "success",
            "fake": True,
            "details": record,
        }
