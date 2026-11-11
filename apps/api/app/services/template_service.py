"""
Safe Email Template Rendering Subsystem for Agentic HR Core.
Complies with Rule 6 (Untrusted Data: never inject unescaped external text into HTML).
"""

import html
from typing import Any


class TemplateNotFoundError(Exception):
    """Raised when an unknown template name is requested."""


# Pre-defined built-in email templates
BUILTIN_TEMPLATES: dict[str, dict[str, str]] = {
    "candidate_interview_invite": {
        "subject": "Interview Invitation: {role_title} at {company_name}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Interview Scheduled</h2>
    <p>Dear {candidate_name},</p>
    <p>We are pleased to invite you for an interview for the <strong>{role_title}</strong> position at <strong>{company_name}</strong>.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Scheduled Time:</strong> {interview_time}</p>
        <p style="margin: 4px 0;"><strong>Meeting Link:</strong> <a href="{meeting_link}" style="color: #2A5BDB;">{meeting_link}</a></p>
    </div>
    <p>Please ensure you are connected 5 minutes prior to the scheduled start time.</p>
    <p>Best regards,<br/><strong>{company_name} Recruitment Team</strong></p>
</div>""",
        "text": """Dear {candidate_name},

We are pleased to invite you for an interview for the {role_title} position at {company_name}.

Scheduled Time: {interview_time}
Meeting Link: {meeting_link}

Please ensure you are connected 5 minutes prior to the scheduled start time.

Best regards,
{company_name} Recruitment Team""",
    },
    "offer_letter_notification": {
        "subject": "Formal Job Offer: {role_title} at {company_name}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Congratulations!</h2>
    <p>Dear {candidate_name},</p>
    <p>We are thrilled to extend an offer of employment for the position of <strong>{role_title}</strong> at <strong>{company_name}</strong>.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Anticipated Start Date:</strong> {start_date}</p>
        <p style="margin: 4px 0;"><strong>Offer Expiration Date:</strong> {expiry_date}</p>
    </div>
    <p>Please review and sign your offer documents in the portal.</p>
    <p>Warm regards,<br/><strong>{company_name} People Team</strong></p>
</div>""",
        "text": """Dear {candidate_name},

We are thrilled to extend an offer of employment for the position of {role_title} at {company_name}.

Anticipated Start Date: {start_date}
Offer Expiration Date: {expiry_date}

Please review and sign your offer documents in the portal.

Warm regards,
{company_name} People Team""",
    },
    "approval_request_alert": {
        "subject": "Action Required: {approval_type} for {applicant_name}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Approval Required</h2>
    <p>Hello {approver_name},</p>
    <p>A new <strong>{approval_type}</strong> requires your review and decision.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Applicant:</strong> {applicant_name}</p>
        <p style="margin: 4px 0;"><strong>Details:</strong> {details_summary}</p>
    </div>
    <p><a href="{portal_url}" style="background-color: #2A5BDB; color: #ffffff; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">Review Request</a></p>
    <p>Best regards,<br/><strong>Agentic HR Core</strong></p>
</div>""",
        "text": """Hello {approver_name},

A new {approval_type} requires your review and decision.

Applicant: {applicant_name}
Details: {details_summary}
Review URL: {portal_url}

Best regards,
Agentic HR Core""",
    },
    "system_test_email": {
        "subject": "System Test Email: {integration_name} Connection Verified",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Connector Test Successful</h2>
    <p>Hello {recipient_name},</p>
    <p>This is an automated system verification email sent by <strong>Agentic HR Core</strong>.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Integration:</strong> {integration_name}</p>
        <p style="margin: 4px 0;"><strong>Sender Account:</strong> {sender_email}</p>
        <p style="margin: 4px 0;"><strong>Timestamp:</strong> {test_timestamp}</p>
        <p style="margin: 4px 0;"><strong>Environment:</strong> {environment}</p>
    </div>
    <p>All Google Workspace connector protocols and security invariants are operating normally.</p>
    <p>Best regards,<br/><strong>Agentic HR Infrastructure</strong></p>
</div>""",
        "text": """Hello {recipient_name},

This is an automated system verification email sent by Agentic HR Core.

Integration: {integration_name}
Sender Account: {sender_email}
Timestamp: {test_timestamp}
Environment: {environment}

All Google Workspace connector protocols and security invariants are operating normally.

Best regards,
Agentic HR Infrastructure""",
    },
    "document_expiry_alert": {
        "subject": "Action Required: Expiration Notice for {document_name}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Document Expiration Notice</h2>
    <p>Dear {employee_name},</p>
    <p>This is an important reminder regarding your mandatory compliance documentation.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Document:</strong> {document_name}</p>
        <p style="margin: 4px 0;"><strong>Expiration Date:</strong> {expiry_date}</p>
        <p style="margin: 4px 0;"><strong>Action Required:</strong> Please submit updated credentials before this date.</p>
    </div>
    <p><a href="{action_url}" style="background-color: #2A5BDB; color: #ffffff; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">Upload Document</a></p>
    <p>Best regards,<br/><strong>{company_name} Compliance Team</strong></p>
</div>""",
        "text": """Dear {employee_name},

This is an important reminder regarding your mandatory compliance documentation.

Document: {document_name}
Expiration Date: {expiry_date}
Action Required: Please submit updated credentials before this date.
Upload URL: {action_url}

Best regards,
{company_name} Compliance Team""",
    },
}


def render_email(
    template_name: str,
    variables: dict[str, Any],
) -> tuple[str, str, str]:
    """
    Renders an email template returning (subject, html_body, text_body).
    Guarantees HTML safety: all variables are strictly escaped before substitution into HTML.
    Plain-text substitution retains unescaped characters.
    """
    if template_name not in BUILTIN_TEMPLATES:
        raise TemplateNotFoundError(f"Template '{template_name}' not found.")

    tmpl = BUILTIN_TEMPLATES[template_name]

    # 1. Plain-text versions of variables (clean string conversion)
    text_vars = {k: str(v if v is not None else "") for k, v in variables.items()}

    # 2. HTML-escaped versions of variables (prevents XSS / HTML injection)
    html_vars = {k: html.escape(str(v if v is not None else "")) for k, v in variables.items()}

    # Render safely using format_map with fallback for missing keys
    class SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return f"{{{key}}}"

    subject = tmpl["subject"].format_map(SafeDict(text_vars))
    html_body = tmpl["html"].format_map(SafeDict(html_vars))
    text_body = tmpl["text"].format_map(SafeDict(text_vars))

    return subject, html_body, text_body
