from typing import Any

from app.integrations.base import EmailMessage, EmailService


class GoogleGmailService(EmailService):
    """
    Real Google Gmail service client.
    Enforces ADR 006 & docs/oauth-scopes.md:
    Scope: https://www.googleapis.com/auth/gmail.send ONLY.
    Never requests or allows gmail.readonly or gmail.modify.
    """

    ALLOWED_SCOPE = "https://www.googleapis.com/auth/gmail.send"

    def __init__(self, credentials: Any = None) -> None:
        self.credentials = credentials

    async def send_email(self, message: EmailMessage) -> dict[str, Any]:
        if not self.credentials:
            raise RuntimeError(
                "Real Google Gmail integration requires valid credentials. "
                "In Stage 1, ensure USE_FAKE_INTEGRATIONS=true."
            )
        # Real sending logic with Google API Client library in later stages
        raise NotImplementedError("Real Gmail sending will be active in later production stages.")
