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
        <p style="margin: 4px 0;"><strong>Days Remaining:</strong> {days_until_expiry}</p>
        <p style="margin: 4px 0;"><strong>Action Required:</strong> Please submit updated credentials before this date.</p>
    </div>
    <p><a href="{action_url}" style="background-color: #2A5BDB; color: #ffffff; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">Upload Document</a></p>
    <p>Best regards,<br/><strong>{company_name} Compliance Team</strong></p>
</div>""",
        "text": """Dear {employee_name},

This is an important reminder regarding your mandatory compliance documentation.

Document: {document_name}
Expiration Date: {expiry_date}
Days Remaining: {days_until_expiry}
Action Required: Please submit updated credentials before this date.
Upload URL: {action_url}

Best regards,
{company_name} Compliance Team""",
    },
    "onboarding_invitation": {
        "subject": "Welcome to {company_name} on Agentic HR",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Welcome to {company_name}</h2>
    <p>Hello {employee_name},</p>
    <p>Your HR workspace for <strong>{company_name}</strong> is now live on <strong>Agentic HR Core</strong>.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Role:</strong> {job_title}</p>
        <p style="margin: 4px 0;"><strong>Department:</strong> {department_name}</p>
        <p style="margin: 4px 0;"><strong>Workspace Portal:</strong> <a href="{portal_url}" style="color: #2A5BDB;">{portal_url}</a></p>
    </div>
    <p>Sign in with your company email to view your profile, company policies, and team directory.</p>
    <p>Warm regards,<br/><strong>{company_name} People Operations</strong></p>
</div>""",
        "text": """Hello {employee_name},

Your HR workspace for {company_name} is now live on Agentic HR Core.

Role: {job_title}
Department: {department_name}
Workspace Portal: {portal_url}

Sign in with your company email to view your profile, company policies, and team directory.

Warm regards,
{company_name} People Operations""",
    },
    "leave_request_submitted": {
        "subject": "Leave Request Pending Review: {employee_name} ({leave_type_name})",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Leave Request Pending Approval</h2>
    <p>Hello {approver_name},</p>
    <p><strong>{employee_name}</strong> has submitted a <strong>{leave_type_name}</strong> request.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Dates:</strong> {date_range}</p>
        <p style="margin: 4px 0;"><strong>Working Days:</strong> {working_days}</p>
        <p style="margin: 4px 0;"><strong>Policy Status:</strong> {policy_status}</p>
        <p style="margin: 4px 0;"><strong>Reason:</strong> {reason}</p>
    </div>
    <p>Please review and decide in the Agentic HR Leave Inbox.</p>
</div>""",
        "text": """Hello {approver_name},

{employee_name} has submitted a {leave_type_name} request.
Dates: {date_range}
Working Days: {working_days}
Policy Status: {policy_status}
Reason: {reason}

Please review and decide in the Agentic HR Leave Inbox.""",
    },
    "leave_request_reminder": {
        "subject": "Reminder: Pending Leave Request from {employee_name}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">24-Hour Leave Approval Reminder</h2>
    <p>Hello {approver_name},</p>
    <p>A leave request from <strong>{employee_name}</strong> ({leave_type_name}, {date_range}) is still awaiting your decision.</p>
    <p>If no action is taken, it will automatically escalate to the backup approver.</p>
</div>""",
        "text": """Hello {approver_name},

Reminder: {employee_name}'s {leave_type_name} request ({date_range}) is still awaiting your decision.
If no action is taken, it will escalate to the backup approver.""",
    },
    "leave_request_escalated": {
        "subject": "Escalated Leave Request: {employee_name} ({leave_type_name})",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Escalated Leave Request</h2>
    <p>Hello {approver_name},</p>
    <p>A leave request from <strong>{employee_name}</strong> ({leave_type_name}, {date_range}, {working_days} day(s)) has been escalated to you as backup approver.</p>
    <p>Please review and decide before it expires.</p>
</div>""",
        "text": """Hello {approver_name},

Escalated: {employee_name}'s {leave_type_name} request ({date_range}, {working_days} day(s)) has been assigned to you as backup approver.""",
    },
    "leave_decision_notification": {
        "subject": "Leave Request {decision_label}: {leave_type_name} ({date_range})",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Leave Request {decision_label}</h2>
    <p>Hello {employee_name},</p>
    <p>Your <strong>{leave_type_name}</strong> request for <strong>{date_range}</strong> ({working_days} working day(s)) is now <strong>{decision_label}</strong>.</p>
    <p><strong>Note:</strong> {decision_note}</p>
</div>""",
        "text": """Hello {employee_name},

Your {leave_type_name} request for {date_range} ({working_days} working day(s)) is now {decision_label}.
Note: {decision_note}""",
    },
    "ticket_created": {
        "subject": "[{ticket_number}] New Ticket Assigned: {subject}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">New Helpdesk Ticket Assigned</h2>
    <p>Hello {assignee_name},</p>
    <p>Ticket <strong>{ticket_number}</strong> has been created by <strong>{requester_name}</strong> and assigned to you.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Subject:</strong> {subject}</p>
        <p style="margin: 4px 0;"><strong>Category:</strong> {category}</p>
        <p style="margin: 4px 0;"><strong>Priority:</strong> {priority}</p>
        <p style="margin: 4px 0;"><strong>SLA Due (PKT):</strong> {sla_due_pkt}</p>
    </div>
</div>""",
        "text": """Hello {assignee_name},

Ticket {ticket_number} has been created by {requester_name} and assigned to you.
Subject: {subject}
Category: {category}
Priority: {priority}
SLA Due (PKT): {sla_due_pkt}""",
    },
    "ticket_public_reply": {
        "subject": "[{ticket_number}] New Reply on Your Ticket: {subject}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Ticket Update: {ticket_number}</h2>
    <p>Hello {requester_name},</p>
    <p><strong>{author_name}</strong> posted a reply on your ticket <strong>{subject}</strong>:</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;">{reply_preview}</p>
    </div>
</div>""",
        "text": """Hello {requester_name},

{author_name} posted a reply on your ticket {ticket_number} ({subject}):
{reply_preview}""",
    },
    "ticket_status_updated": {
        "subject": "[{ticket_number}] Ticket {status_label}: {subject}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Ticket {status_label}</h2>
    <p>Hello {requester_name},</p>
    <p>Your Helpdesk ticket <strong>{ticket_number}</strong> (<strong>{subject}</strong>) is now <strong>{status_label}</strong>.</p>
    <div style="background-color: #F8F9FD; border-left: 4px solid #2A5BDB; padding: 12px; margin: 16px 0;">
        <p style="margin: 4px 0;"><strong>Reopen Window Until (PKT):</strong> {reopen_until_pkt}</p>
    </div>
    <p>You may rate your support experience or reopen the ticket within the reopen window.</p>
</div>""",
        "text": """Hello {requester_name},

Your Helpdesk ticket {ticket_number} ({subject}) is now {status_label}.
Reopen Window Until (PKT): {reopen_until_pkt}""",
    },
    "ticket_sla_reminder": {
        "subject": "SLA Reminder: [{ticket_number}] {subject}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #2A5BDB;">Ticket SLA Pre-Breach Reminder</h2>
    <p>Hello {assignee_name},</p>
    <p>Ticket <strong>{ticket_number}</strong> (<strong>{subject}</strong>) is approaching its SLA deadline.</p>
    <p><strong>SLA Due (PKT):</strong> {sla_due_pkt}</p>
</div>""",
        "text": """Hello {assignee_name},

Reminder: Ticket {ticket_number} ({subject}) is approaching its SLA deadline ({sla_due_pkt} PKT).""",
    },
    "ticket_sla_escalated": {
        "subject": "SLA BREACH ESCALATION: [{ticket_number}] {subject}",
        "html": """<div style="font-family: Arial, sans-serif; color: #102C54; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2 style="color: #B91C1C;">Ticket SLA Breached — Escalated</h2>
    <p>Hello {escalation_target_name},</p>
    <p>Ticket <strong>{ticket_number}</strong> (<strong>{subject}</strong>, {category}, priority: {priority}) breached its SLA deadline at <strong>{sla_due_pkt}</strong> PKT and has been escalated to you.</p>
</div>""",
        "text": """Hello {escalation_target_name},

Escalation: Ticket {ticket_number} ({subject}, {category}, priority: {priority}) breached its SLA deadline at {sla_due_pkt} PKT.""",
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
