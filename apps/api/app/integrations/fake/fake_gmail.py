"""
Fake Gmail Service with deterministic in-memory storage and fault injection.
Enforces ADR 006: Stage 1/3 USE_FAKE_INTEGRATIONS=true, send-only scope.
"""

import uuid
from typing import Any

from app.integrations.base import EmailMessage, EmailService, GmailDeliveryError
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)


class FakeGmailService(EmailService):
    """
    Fake Gmail service that records sent messages in memory.
    Enforces ADR 006: Stage 1 USE_FAKE_INTEGRATIONS=true, no real emails sent.
    """

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.simulate_401_on_next_call: bool = False
        self.simulate_429_on_next_call: bool = False
        self.simulate_503_on_next_call: bool = False
        self.simulate_delivery_error_on_next_call: bool = False
        # Backward compatibility aliases
        self.simulate_401_on_next_send: bool = False
        self.simulate_delivery_error_on_next_send: bool = False

    def reset(self) -> None:
        """Resets in-memory storage and fault injection flags."""
        self.sent_messages.clear()
        self.simulate_401_on_next_call = False
        self.simulate_429_on_next_call = False
        self.simulate_503_on_next_call = False
        self.simulate_delivery_error_on_next_call = False
        self.simulate_401_on_next_send = False
        self.simulate_delivery_error_on_next_send = False

    async def send_email(
        self,
        message: EmailMessage,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        if (
            self.simulate_401_on_next_call
            or self.simulate_401_on_next_send
            or "simulate-401" in message.to_email
        ):
            self.simulate_401_on_next_call = False
            self.simulate_401_on_next_send = False
            raise ConnectorAuthExpiredError(
                "Simulated Google OAuth 401 Unauthorized / invalid_grant failure."
            )

        if self.simulate_429_on_next_call:
            self.simulate_429_on_next_call = False
            raise ConnectorRateLimitError(
                "Simulated Google Gmail 429 Too Many Requests.",
                retry_after=1,
            )

        if self.simulate_503_on_next_call:
            self.simulate_503_on_next_call = False
            raise ConnectorServiceUnavailableError(
                "Simulated Google Gmail 503 Service Unavailable."
            )

        if (
            self.simulate_delivery_error_on_next_call
            or self.simulate_delivery_error_on_next_send
            or "simulate-fail" in message.to_email
            or "simulate-bounce" in message.to_email
        ):
            self.simulate_delivery_error_on_next_call = False
            self.simulate_delivery_error_on_next_send = False
            raise GmailDeliveryError("550 5.1.1 Recipient address rejected: User unknown")

        msg_id = f"fake_msg_{uuid.uuid4().hex[:8]}"
        record = {
            "id": msg_id,
            "status": "SENT_FAKE",
            "to": message.to_email,
            "subject": message.subject,
            "body_text": message.body_text,
            "body_html": message.body_html,
            "sender": message.sender_email or "agentic.hr.test.ubaid.owner@gmail.com",
            "cc": message.cc,
            "bcc": message.bcc,
            "reply_to": message.reply_to,
            "headers": message.headers,
        }
        self.sent_messages.append(record)
        return {
            "message_id": msg_id,
            "status": "success",
            "fake": True,
            "details": record,
        }
