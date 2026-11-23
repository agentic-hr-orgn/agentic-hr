"""Data models for database and schemas."""

from app.models.approval import Approval
from app.models.approval_revision import ApprovalRevision
from app.models.attendance import Attendance, AttendanceMonthLock
from app.models.audit_log import AuditLog
from app.models.calendar import CalendarEventRecord
from app.models.chat import AnswerFeedback, ChatMessage, ChatSession
from app.models.connector import ConnectorCredential, SentMessage
from app.models.department import Department
from app.models.drive import DriveFileRecord
from app.models.employee import Employee
from app.models.employee_document import EmployeeDocument
from app.models.idempotency import IdempotencyRecord
from app.models.leave import (
    Holiday,
    LeaveBalance,
    LeaveLedger,
    LeaveRequest,
    LeaveType,
    WorkWeek,
)
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.onboarding_state import OnboardingState
from app.models.outbox import OutboxEvent
from app.models.policy_chunk import PolicyChunk
from app.models.policy_chunk_embedding import PolicyChunkEmbedding
from app.models.policy_document import PolicyDocument
from app.models.policy_document_page import PolicyDocumentPage
from app.models.role import Role
from app.models.sheets import SheetsDriftLog, SheetsSyncQueue
from app.models.synthetic import (
    CompanyProfile,
    EmployeeRecord,
)
from app.models.synthetic import (
    PolicyDocument as SyntheticPolicyDocument,
)
from app.models.tenant import TenantBase
from app.models.tenant_entity import Tenant
from app.models.ticket import Ticket, TicketAssignmentRule, TicketMessage
from app.models.user import User

__all__ = [
    "AnswerFeedback",
    "Approval",
    "ApprovalRevision",
    "Attendance",
    "AttendanceMonthLock",
    "AuditLog",
    "CalendarEventRecord",
    "ChatMessage",
    "ChatSession",
    "CompanyProfile",
    "ConnectorCredential",
    "Department",
    "DriveFileRecord",
    "Employee",
    "EmployeeDocument",
    "EmployeeRecord",
    "Holiday",
    "IdempotencyRecord",
    "LeaveBalance",
    "LeaveLedger",
    "LeaveRequest",
    "LeaveType",
    "Membership",
    "Notification",
    "OnboardingState",
    "OutboxEvent",
    "PolicyChunk",
    "PolicyChunkEmbedding",
    "PolicyDocument",
    "PolicyDocumentPage",
    "Role",
    "SentMessage",
    "SheetsDriftLog",
    "SheetsSyncQueue",
    "SyntheticPolicyDocument",
    "Tenant",
    "TenantBase",
    "Ticket",
    "TicketAssignmentRule",
    "TicketMessage",
    "User",
    "WorkWeek",
]
