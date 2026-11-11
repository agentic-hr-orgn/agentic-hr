import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

from app.integrations.base import EmailMessage, EmailService, GmailDeliveryError
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)


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

    @staticmethod
    def build_mime_message(message: EmailMessage) -> MIMEMultipart:
        """
        Constructs an RFC 2822 multipart/alternative MIME message
        containing both text/plain and text/html representations.
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = message.subject
        if message.sender_email:
            msg["From"] = message.sender_email
        msg["To"] = message.to_email
        if message.cc:
            msg["Cc"] = ", ".join(message.cc)
        if message.bcc:
            msg["Bcc"] = ", ".join(message.bcc)
        if message.reply_to:
            msg["Reply-To"] = message.reply_to
        for header_name, header_value in message.headers.items():
            msg[header_name] = header_value

        # Always attach plain text part first
        part_text = MIMEText(message.body_text, "plain", "utf-8")
        msg.attach(part_text)

        # Attach HTML part as alternative if provided
        if message.body_html:
            part_html = MIMEText(message.body_html, "html", "utf-8")
            msg.attach(part_html)

        return msg

    @staticmethod
    def encode_message_raw(mime_msg: MIMEMultipart) -> str:
        """Base64URL-encodes RFC 2822 MIME message for Google Gmail API."""
        return base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("ascii")

    async def send_email(
        self,
        message: EmailMessage,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        token = access_token or (
            self.credentials.token if hasattr(self.credentials, "token") else None
        )
        if not token:
            raise ConnectorAuthExpiredError(
                "Missing or expired Google OAuth access token. Reauthorization required."
            )

        mime_msg = self.build_mime_message(message)
        raw_encoded = self.encode_message_raw(mime_msg)

        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {"raw": raw_encoded}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=body)

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "message_id": data.get("id"),
                    "thread_id": data.get("threadId"),
                    "status": "success",
                    "fake": False,
                    "details": data,
                }
            elif resp.status_code == 401:
                raise ConnectorAuthExpiredError(
                    f"Google Gmail API 401 Unauthorized / invalid_grant: {resp.text}"
                )
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 1))
                raise ConnectorRateLimitError(
                    f"Google Gmail API 429 Rate Limit Exceeded: {resp.text}",
                    retry_after=retry_after,
                )
            elif resp.status_code in (500, 502, 503, 504):
                raise ConnectorServiceUnavailableError(
                    f"Google Gmail API {resp.status_code} Service Unavailable: {resp.text}"
                )
            else:
                raise GmailDeliveryError(
                    f"Google Gmail API delivery error ({resp.status_code}): {resp.text}"
                )
