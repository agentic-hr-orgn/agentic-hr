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

// Type-safe OpenAPI client and factory
export * from "./client";

// Auto-generated OpenAPI Schema Contracts
export type * from "./api-client";
