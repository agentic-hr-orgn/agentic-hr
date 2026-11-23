/**
 * @agentic-hr/contracts
 * Shared TypeScript domain models, API response DTOs, and schemas.
 */

import type { components } from "./api-client";

// Derived API DTOs directly from OpenAPI schema (no manual duplication)
export type HealthResponse = components["schemas"]["HealthResponse"];
export type EmployeeRecord = components["schemas"]["EmployeeRecord"];

// Stage 3 Connectors DTOs
export type ConnectorResponse = components["schemas"]["ConnectorResponse"];
export type ConnectDirectRequest = components["schemas"]["ConnectDirectRequest"];
export type ReadOnlyToggleRequest = components["schemas"]["ReadOnlyToggleRequest"];
export type SendEmailRequest = components["schemas"]["SendEmailRequest"];
export type SentMessageResponse = components["schemas"]["SentMessageResponse"];
export type BounceWebhookRequest = components["schemas"]["BounceWebhookRequest"];
export type SheetsSyncRequest = components["schemas"]["SheetsSyncRequest"];
export type SheetsSyncResponse = components["schemas"]["SheetsSyncResponse"];
export type SheetsDriftLogResponse = components["schemas"]["SheetsDriftLogResponse"];
export type SheetsReconcileResponse = components["schemas"]["SheetsReconcileResponse"];

// Stage 4 Onboarding, Employees, Org Chart, Document Vault, Bulk Import & Notifications DTOs
export type OnboardingState = components["schemas"]["OnboardingStateResponse"];
export type ReadinessChecklist = components["schemas"]["OnboardingReadinessResponse"];
export type Department = components["schemas"]["DepartmentResponse"];
export type EmployeeSummary = components["schemas"]["EmployeeListItem"];
export type EmployeeListResponse = components["schemas"]["EmployeeListResponse"];
export type EmployeeProfile = components["schemas"]["EmployeeProfileResponse"];
export type OrgChartNode = components["schemas"]["OrgChartNode"];
export type OrgChartResponse = components["schemas"]["OrgChartResponse"];
export type EmployeeDocument = components["schemas"]["EmployeeDocumentResponse"];
export type ImportValidationResult = components["schemas"]["ImportValidationResult"];
export type NotificationItem = components["schemas"]["NotificationItem"];
export type NotificationListResponse = components["schemas"]["NotificationListResponse"];
export type ExpiryScanSummary = components["schemas"]["ExpiryScanSummary"];
export type PolicyDocumentItem = components["schemas"]["PolicyDocumentResponse"];
export type PolicyPagePreviewItem = components["schemas"]["PolicyPagePreviewItem"];
export type PolicyChunkPreviewItem = components["schemas"]["PolicyChunkPreviewItem"];
export type PolicyPreviewResponse = components["schemas"]["PolicyPreviewResponse"];
export type PolicyChunkUpdateRequest = components["schemas"]["PolicyChunkUpdateRequest"];
export type PolicyPageFixRequest = components["schemas"]["PolicyPageFixRequest"];
export type PolicyPublishRequest = components["schemas"]["PolicyPublishRequest"];
export type PolicyPublishResponse = components["schemas"]["PolicyPublishResponse"];
export type PolicyReviewQueueItem = components["schemas"]["PolicyReviewQueueItem"];
export type PolicyArchiveResponse = components["schemas"]["PolicyArchiveResponse"];
export type PolicyCitation = components["schemas"]["PolicyCitation"];
export type PolicySearchRequest = components["schemas"]["PolicySearchRequest"];
export type PolicySearchHit = components["schemas"]["PolicySearchHit"];
export type PolicySearchResponse = components["schemas"]["PolicySearchResponse"];
export type OnboardingStepSaveRequest = components["schemas"]["OnboardingStepSaveRequest"];
export type OnboardingLaunchResponse = components["schemas"]["OnboardingLaunchResponse"];
export type DepartmentCreateRequest = components["schemas"]["DepartmentCreateRequest"];
export type DepartmentUpdateRequest = components["schemas"]["DepartmentUpdateRequest"];
export type ImportCommitResponse = components["schemas"]["ImportCommitResponse"];
export type ImportPreviewRow = components["schemas"]["ImportPreviewRow"];
export type ImportRowError = components["schemas"]["ImportRowError"];
export type EmployeeCreateRequest = components["schemas"]["EmployeeCreateRequest"];
export type EmployeeUpdateRequest = components["schemas"]["EmployeeUpdateRequest"];
export type MarkAllReadResponse = components["schemas"]["MarkAllReadResponse"];
export type TriggerExpiryScanRequest = components["schemas"]["TriggerExpiryScanRequest"];

// Stage 6 Leave & Work-Week Configuration DTOs
export type WorkWeekResponse = components["schemas"]["WorkWeekResponse"];
export type WorkWeekUpdateRequest = components["schemas"]["WorkWeekUpdateRequest"];
export type LeaveTypeResponse = components["schemas"]["LeaveTypeResponse"];
export type LeaveTypeCreateRequest = components["schemas"]["LeaveTypeCreateRequest"];
export type LeaveTypeUpdateRequest = components["schemas"]["LeaveTypeUpdateRequest"];
export type LeaveTypeDeleteResponse = components["schemas"]["LeaveTypeDeleteResponse"];
export type HolidayResponse = components["schemas"]["HolidayResponse"];
export type HolidayCreateRequest = components["schemas"]["HolidayCreateRequest"];
export type HolidayUpdateRequest = components["schemas"]["HolidayUpdateRequest"];
export type HolidayDeleteResponse = components["schemas"]["HolidayDeleteResponse"];

// Stage 6 Leave Intake, Requests, Balances & Overview DTOs
export type LeaveIntakeRequest = components["schemas"]["LeaveIntakeRequest"];
export type LeaveIntakeResponse = components["schemas"]["LeaveIntakeResponse"];
export type LeaveConfirmCard = components["schemas"]["LeaveConfirmCard"];
export type LeaveRequestCreateRequest = components["schemas"]["LeaveRequestCreateRequest"];
export type LeaveDecisionRequest = components["schemas"]["LeaveDecisionRequest"];
export type LeaveCancelRequest = components["schemas"]["LeaveCancelRequest"];
export type LeaveRequestResponse = components["schemas"]["LeaveRequestResponse"];
export type LeaveBalanceItemResponse = components["schemas"]["LeaveBalanceItemResponse"];
export type TeamCalendarItemResponse = components["schemas"]["TeamCalendarItemResponse"];
export type LeaveOverviewResponse = components["schemas"]["LeaveOverviewResponse"];

// Stage 6 Attendance, Daily Grid, Import, Auto-Fill, Month Lock & Post-Lock Edit DTOs
export type AttendanceRecordResponse = components["schemas"]["AttendanceRecordResponse"];
export type AttendanceTodayResponse = components["schemas"]["AttendanceTodayResponse"];
export type AttendanceCheckInRequest = components["schemas"]["AttendanceCheckInRequest"];
export type DailyAttendanceGridRow = components["schemas"]["DailyAttendanceGridRow"];
export type DailyAttendanceGridResponse = components["schemas"]["DailyAttendanceGridResponse"];
export type DailyAttendanceUpsertItem = components["schemas"]["DailyAttendanceUpsertItem"];
export type DailyAttendanceUpsertRequest = components["schemas"]["DailyAttendanceUpsertRequest"];
export type DailyAttendanceUpsertResponse = components["schemas"]["DailyAttendanceUpsertResponse"];
export type AttendanceImportRowError = components["schemas"]["AttendanceImportRowError"];
export type AttendanceImportPreviewRow = components["schemas"]["AttendanceImportPreviewRow"];
export type AttendanceImportValidationResult =
  components["schemas"]["AttendanceImportValidationResult"];
export type AttendanceImportCommitResponse =
  components["schemas"]["AttendanceImportCommitResponse"];
export type AttendanceAutoFillRequest = components["schemas"]["AttendanceAutoFillRequest"];
export type AttendanceAutoFillResponse = components["schemas"]["AttendanceAutoFillResponse"];
export type EmployeeMonthGapItem = components["schemas"]["EmployeeMonthGapItem"];
export type AttendanceMonthReviewResponse = components["schemas"]["AttendanceMonthReviewResponse"];
export type AttendanceMonthLockResponse = components["schemas"]["AttendanceMonthLockResponse"];
export type AttendancePostLockEditRequest = components["schemas"]["AttendancePostLockEditRequest"];
export type AttendancePostLockEditResponse =
  components["schemas"]["AttendancePostLockEditResponse"];

// Google Drive DTOs
export interface DriveFolderEnsureRequest {
  folder_name?: string;
  parent_folder_id?: string | null;
}

export interface DriveFolderResponse {
  folder_id: string;
  name: string;
  web_view_link?: string | null;
  parent_folder_id?: string | null;
}

export interface DriveUploadRequest {
  name: string;
  content_base64: string;
  mime_type: string;
  folder_id?: string | null;
}

export interface DriveFileResponse {
  id: string;
  tenant_id: string;
  drive_file_id: string;
  folder_id?: string | null;
  name: string;
  mime_type: string;
  size_bytes: number;
  web_view_link?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DriveFileListResponse {
  files: DriveFileResponse[];
  total: number;
}

// Google Calendar DTOs
export interface TimeSlotSchema {
  start_time: string;
  end_time: string;
}

export interface FreeBusyQueryRequest {
  calendar_ids?: string[];
  time_min: string;
  time_max: string;
}

export interface FreeBusyQueryResponse {
  calendars: Record<string, TimeSlotSchema[]>;
}

export interface CalendarEventCreateRequest {
  title: string;
  start_time: string;
  end_time: string;
  attendees?: string[];
  description?: string | null;
  location?: string | null;
  calendar_id?: string;
  timezone?: string;
}

export interface CalendarEventUpdateRequest {
  title?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  attendees?: string[] | null;
  description?: string | null;
  location?: string | null;
}

export interface CalendarEventResponse {
  id: string;
  tenant_id: string;
  google_event_id: string;
  title: string;
  start_time: string;
  end_time: string;
  timezone: string;
  attendees: string[];
  description?: string | null;
  location?: string | null;
  hangout_link?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface TenantContext {
  tenant_id: string;
  user_id?: string;
  role?: string;
}

export interface Employee {
  employee_id: string;
  name: string;
  email: string;
  department: string;
  role: string;
  status: string;
  is_test_account: boolean;
}

export interface PolicyDocument {
  title: string;
  language: "en" | "ur" | string;
  category: string;
  summary: string;
}

export interface CompanyProfile {
  name: string;
  domain: string;
  departments: string[];
  total_employees: number;
  test_accounts: string[];
  policy_documents: PolicyDocument[];
}

export type AgentRole = "DocuVerify" | "Screener" | "Code Tester" | "Policy Brain";

export interface AgentStatus {
  id: string;
  role: AgentRole;
  name: string;
  model: string;
  status: "idle" | "running" | "completed" | "error";
  lastRun?: string;
}

// ============================================================================
// Stage 7 Policy Brain Chat, Citations, SSE Streaming & Policy Gaps DTOs
// ============================================================================

export type ChatLanguage = "en" | "ur" | "roman_ur" | "mixed";
export type ChatRole = "user" | "assistant" | "system";
export type FeedbackRating = "up" | "down";

export type TicketCategory =
  "hr_policy" | "leave_attendance" | "payroll" | "it_access" | "facilities" | "general";
export type TicketPriority = "low" | "medium" | "high" | "urgent";
export type TicketStatus = "open" | "in_progress" | "waiting_on_employee" | "resolved" | "closed";
export type TicketSource = "chat_abstain" | "chat_ask_hr" | "helpdesk_agent" | "manual";
export type AssigneeRole = "hr_head" | "owner" | "manager";

export interface CitationItem {
  chunk_id: string;
  document_id: string;
  title: string;
  version: number;
  section: string;
  page?: number | string | null;
  snippet: string;
}

export interface ToolCallTrace {
  tool_name: string;
  status: "ok" | "error" | "denied";
  summary?: string | null;
}

export interface ChatSession {
  id: string;
  tenant_id: string;
  user_id: string;
  title: string;
  language: ChatLanguage;
  last_message_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatSessionCreateRequest {
  title?: string | null;
  language?: ChatLanguage;
}

export interface ChatMessage {
  id: string;
  tenant_id: string;
  session_id: string;
  role: ChatRole;
  content: string;
  language: ChatLanguage;
  intent?: string | null;
  citations: CitationItem[];
  tool_calls: ToolCallTrace[];
  abstained: boolean;
  ticket_id?: string | null;
  ticket_number?: string | null;
  handoff_payload?: Record<string, unknown> | null;
  topic_tag?: string | null;
  latency_ms?: number | null;
  model_tier?: string | null;
  token_usage?: Record<string, unknown> | null;
  cost_usd?: string | number | null;
  created_at: string;
}

export interface ChatTurnRequest {
  message: string;
  language?: ChatLanguage | null;
  start_workflow?: boolean;
}

export interface ChatTurnResponse {
  session_id: string;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}

export interface PolicyBrainStreamDonePayload {
  message_id: string;
  session_id: string;
  answer: string;
  language: ChatLanguage;
  intent: string;
  citations: CitationItem[];
  tool_calls: ToolCallTrace[];
  abstained: boolean;
  ticket_id?: string | null;
  ticket_number?: string | null;
  handoff_payload?: Record<string, unknown> | null;
  topic_tag?: string | null;
  latency_ms?: number | null;
}

export type PolicyBrainStreamEvent =
  | { type: "status"; data: { node: string } }
  | { type: "token"; data: { text: string } }
  | { type: "done"; data: PolicyBrainStreamDonePayload }
  | { type: "error"; data: { detail: string } };

export interface AnswerFeedbackRequest {
  rating: FeedbackRating;
  comment?: string | null;
  topic_tag?: string | null;
}

export interface AnswerFeedbackResponse {
  id: string;
  tenant_id: string;
  message_id: string;
  user_id: string;
  rating: FeedbackRating;
  comment?: string | null;
  topic_tag?: string | null;
  created_at: string;
}

export interface AskHrRequest {
  category?: TicketCategory | null;
  priority?: TicketPriority;
  note?: string | null;
  start_workflow?: boolean;
}

export interface AskHrResponse {
  ticket_id: string;
  ticket_number: string;
  category: TicketCategory;
  priority: TicketPriority;
  message_id: string;
}

export interface PolicyGapGroup {
  topic_tag: string;
  total_count: number;
  abstained_count: number;
  downvoted_count: number;
  sample_questions: string[];
  ticket_numbers: string[];
  latest_at: string;
}

export interface PolicyGapsResponse {
  total_gaps: number;
  groups: PolicyGapGroup[];
}

// ============================================================================
// Stage 7 Helpdesk Ticketing, Assignment Rules & Read-Only Agent DTOs
// ============================================================================

export interface TicketMessage {
  id: string;
  tenant_id: string;
  ticket_id: string;
  author_user_id: string;
  body: string;
  is_internal: boolean;
  created_at: string;
}

export interface Ticket {
  id: string;
  tenant_id: string;
  ticket_number: string;
  requester_user_id: string;
  assigned_to_user_id?: string | null;
  category: TicketCategory;
  priority: TicketPriority;
  status: TicketStatus;
  subject: string;
  description: string;
  source: TicketSource;
  source_session_id?: string | null;
  sla_due_at: string;
  sla_reminder_at: string;
  sla_reminder_sent_at?: string | null;
  sla_escalated_at?: string | null;
  resolved_at?: string | null;
  closed_at?: string | null;
  reopen_until?: string | null;
  satisfaction_rating?: number | null;
  satisfaction_comment?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TicketDetail extends Ticket {
  messages: TicketMessage[];
}

export interface TicketSummaryItem {
  ticket_id: string;
  tenant_id: string;
  ticket_number: string;
  requester_user_id: string;
  requester_name?: string | null;
  assigned_to_user_id?: string | null;
  assignee_name?: string | null;
  category: TicketCategory;
  priority: TicketPriority;
  status: TicketStatus;
  subject: string;
  source: TicketSource;
  sla_due_at: string;
  sla_reminder_at: string;
  sla_escalated_at?: string | null;
  is_sla_breached: boolean;
  public_message_count: number;
  resolved_at?: string | null;
  closed_at?: string | null;
  satisfaction_rating?: number | null;
  created_at: string;
  updated_at: string;
}

export interface TicketCreateRequest {
  category?: TicketCategory;
  priority?: TicketPriority;
  subject: string;
  description: string;
  source?: TicketSource;
  source_session_id?: string | null;
  assigned_to_user_id?: string | null;
  start_workflow?: boolean;
  reminder_delay_seconds?: number | null;
  breach_delay_seconds?: number | null;
}

export interface TicketUpdateRequest {
  status?: TicketStatus | null;
  priority?: TicketPriority | null;
  category?: TicketCategory | null;
  assigned_to_user_id?: string | null;
}

export interface TicketMessageCreateRequest {
  body: string;
  is_internal?: boolean;
}

export interface TicketRateRequest {
  satisfaction_rating?: number | null;
  satisfaction_comment?: string | null;
}

export interface TicketReopenRequest {
  reason: string;
  start_workflow?: boolean;
  reminder_delay_seconds?: number | null;
  breach_delay_seconds?: number | null;
}

export interface TicketAssignmentRule {
  id: string;
  tenant_id: string;
  category: TicketCategory;
  default_assignee_user_id?: string | null;
  default_assignee_role: AssigneeRole;
  sla_hours: number;
  reminder_before_hours: number;
  reopen_window_days: number;
  created_at: string;
  updated_at: string;
}

export interface TicketRuleUpdateRequest {
  default_assignee_user_id?: string | null;
  default_assignee_role?: AssigneeRole;
  sla_hours?: number;
  reminder_before_hours?: number;
  reopen_window_days?: number;
}

export interface HelpdeskAskRequest {
  question: string;
  start_workflow?: boolean;
  reminder_delay_seconds?: number | null;
  breach_delay_seconds?: number | null;
}

export interface HelpdeskAskResponse {
  answered: boolean;
  answer: string;
  language: ChatLanguage;
  citations: CitationItem[];
  ticket?: Ticket | null;
  ticket_number?: string | null;
  category?: TicketCategory | null;
  priority?: TicketPriority | null;
  privileged_request_blocked: boolean;
}

// Type-safe OpenAPI client and factory
export * from "./client";

// Auto-generated OpenAPI Schema Contracts
export type * from "./api-client";
