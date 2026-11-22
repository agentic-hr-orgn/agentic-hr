"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  HolidayCreateRequest,
  HolidayDeleteResponse,
  HolidayResponse,
  HolidayUpdateRequest,
  LeaveBalanceItemResponse,
  LeaveCancelRequest,
  LeaveDecisionRequest,
  LeaveIntakeRequest,
  LeaveIntakeResponse,
  LeaveOverviewResponse,
  LeaveRequestCreateRequest,
  LeaveRequestResponse,
  LeaveTypeCreateRequest,
  LeaveTypeDeleteResponse,
  LeaveTypeResponse,
  LeaveTypeUpdateRequest,
  TeamCalendarItemResponse,
  WorkWeekResponse,
  WorkWeekUpdateRequest,
} from "@agentic-hr/contracts";
import { ApiHttpError, ApiOfflineError, apiRequest } from "@/lib/api-request";
import { getAuthHeader } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

function buildAuthHeaders(authHeader: string | null, json = false): Record<string, string> {
  const headers: Record<string, string> = {};
  if (json) {
    headers["Content-Type"] = "application/json";
  }
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

function unwrapApiResult<T>(
  res: Awaited<ReturnType<typeof apiRequest<T>>>,
  offlineMessage = "OFFLINE: FastAPI backend is unreachable."
): T {
  if (res.kind === "live") return res.data;
  if (res.kind === "http_error") {
    throw new ApiHttpError(res.status, res.code, res.message, res.body);
  }
  throw new ApiOfflineError(offlineMessage);
}

// ============================================================================
// Configuration Hooks (/api/v1/leave/config/*)
// ============================================================================

export function useWorkWeek(enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<WorkWeekResponse, Error>({
    queryKey: ["leave", "work-week", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<WorkWeekResponse>(
        `${API_BASE_URL}/api/v1/leave/config/work-week`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load work-week settings"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 60,
    retry: false,
  });
}

export function useUpdateWorkWeek() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<WorkWeekResponse, Error, WorkWeekUpdateRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<WorkWeekResponse>(
        `${API_BASE_URL}/api/v1/leave/config/work-week`,
        {
          method: "PUT",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to update work-week settings"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; work-week settings could not be saved."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave", "work-week"] });
      queryClient.invalidateQueries({ queryKey: ["attendance", "me-today"] });
    },
  });
}

export function useLeaveTypes(includeInactive = false, enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<LeaveTypeResponse[], Error>({
    queryKey: ["leave", "types", orgId, includeInactive],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const qs = includeInactive ? "?include_inactive=true" : "";
      const res = await apiRequest<LeaveTypeResponse[]>(
        `${API_BASE_URL}/api/v1/leave/config/types${qs}`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load leave types"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 60,
    retry: false,
  });
}

export function useCreateLeaveType() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<LeaveTypeResponse, Error, LeaveTypeCreateRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<LeaveTypeResponse>(
        `${API_BASE_URL}/api/v1/leave/config/types`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to create leave type"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; leave type could not be created."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave", "types"] });
      queryClient.invalidateQueries({ queryKey: ["leave", "balances"] });
    },
  });
}

export function useUpdateLeaveType() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    LeaveTypeResponse,
    Error,
    { leaveTypeId: string; payload: LeaveTypeUpdateRequest }
  >({
    mutationFn: async ({ leaveTypeId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<LeaveTypeResponse>(
        `${API_BASE_URL}/api/v1/leave/config/types/${leaveTypeId}`,
        {
          method: "PATCH",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to update leave type"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; leave type could not be updated."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave", "types"] });
      queryClient.invalidateQueries({ queryKey: ["leave", "balances"] });
    },
  });
}

export function useDeleteLeaveType() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<LeaveTypeDeleteResponse, Error, string>({
    mutationFn: async (leaveTypeId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<LeaveTypeDeleteResponse>(
        `${API_BASE_URL}/api/v1/leave/config/types/${leaveTypeId}`,
        {
          method: "DELETE",
          headers: buildAuthHeaders(authHeader, true),
        },
        "Failed to delete leave type"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; leave type could not be deleted."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave", "types"] });
    },
  });
}

export function useHolidays(year?: number, enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<HolidayResponse[], Error>({
    queryKey: ["leave", "holidays", orgId, year ?? "all"],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const qs = year ? `?year=${year}` : "";
      const res = await apiRequest<HolidayResponse[]>(
        `${API_BASE_URL}/api/v1/leave/config/holidays${qs}`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load public holidays"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 60,
    retry: false,
  });
}

export function useCreateHoliday() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<HolidayResponse, Error, HolidayCreateRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<HolidayResponse>(
        `${API_BASE_URL}/api/v1/leave/config/holidays`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to create holiday"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; holiday could not be created."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave", "holidays"] });
    },
  });
}

export function useUpdateHoliday() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<HolidayResponse, Error, { holidayId: string; payload: HolidayUpdateRequest }>({
    mutationFn: async ({ holidayId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<HolidayResponse>(
        `${API_BASE_URL}/api/v1/leave/config/holidays/${holidayId}`,
        {
          method: "PATCH",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to update holiday"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; holiday could not be updated."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave", "holidays"] });
    },
  });
}

export function useDeleteHoliday() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<HolidayDeleteResponse, Error, string>({
    mutationFn: async (holidayId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<HolidayDeleteResponse>(
        `${API_BASE_URL}/api/v1/leave/config/holidays/${holidayId}`,
        {
          method: "DELETE",
          headers: buildAuthHeaders(authHeader, true),
        },
        "Failed to delete holiday"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; holiday could not be deleted."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave", "holidays"] });
    },
  });
}

// ============================================================================
// Operational Leave Hooks (/api/v1/leave/*)
// ============================================================================

export function useMyLeaveBalances(year?: number, enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<LeaveBalanceItemResponse[], Error>({
    queryKey: ["leave", "balances", "me", orgId, year ?? "current"],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const qs = year ? `?year=${year}` : "";
      const res = await apiRequest<LeaveBalanceItemResponse[]>(
        `${API_BASE_URL}/api/v1/leave/balances/me${qs}`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load your leave balances"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useAllLeaveBalances(
  params?: { employeeId?: string; year?: number },
  enabled = true
) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const employeeId = params?.employeeId;
  const year = params?.year;

  return useQuery<LeaveBalanceItemResponse[], Error>({
    queryKey: ["leave", "balances", "all", orgId, { employeeId, year }],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const sp = new URLSearchParams();
      if (employeeId) sp.set("employee_id", employeeId);
      if (year) sp.set("year", String(year));
      const qs = sp.toString();
      const res = await apiRequest<LeaveBalanceItemResponse[]>(
        `${API_BASE_URL}/api/v1/leave/balances${qs ? `?${qs}` : ""}`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load tenant leave balances"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useMyLeaveRequests(enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<LeaveRequestResponse[], Error>({
    queryKey: ["leave", "requests", "me", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<LeaveRequestResponse[]>(
        `${API_BASE_URL}/api/v1/leave/requests/me`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load your leave requests"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 20,
    retry: false,
  });
}

export function useLeaveInbox(enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<LeaveRequestResponse[], Error>({
    queryKey: ["leave", "inbox", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<LeaveRequestResponse[]>(
        `${API_BASE_URL}/api/v1/leave/inbox`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load manager leave inbox"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 20,
    retry: false,
  });
}

export function useTeamLeaveCalendar(
  params?: { startDate?: string; endDate?: string; departmentId?: string },
  enabled = true
) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const startDate = params?.startDate;
  const endDate = params?.endDate;
  const departmentId = params?.departmentId;

  return useQuery<TeamCalendarItemResponse[], Error>({
    queryKey: ["leave", "calendar", orgId, { startDate, endDate, departmentId }],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const sp = new URLSearchParams();
      if (startDate) sp.set("start_date", startDate);
      if (endDate) sp.set("end_date", endDate);
      if (departmentId) sp.set("department_id", departmentId);
      const qs = sp.toString();
      const res = await apiRequest<TeamCalendarItemResponse[]>(
        `${API_BASE_URL}/api/v1/leave/calendar${qs ? `?${qs}` : ""}`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load team leave calendar"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useLeaveOverview(statusFilter?: string, enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<LeaveOverviewResponse, Error>({
    queryKey: ["leave", "overview", orgId, statusFilter || "all"],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
      const res = await apiRequest<LeaveOverviewResponse>(
        `${API_BASE_URL}/api/v1/leave/overview${qs}`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load HR leave overview"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useLeaveIntake() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<LeaveIntakeResponse, Error, LeaveIntakeRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const body: LeaveIntakeRequest = {
        message: payload.message.trim(),
        ...(payload.conversation_context
          ? { conversation_context: payload.conversation_context }
          : {}),
        ...(payload.employee_id ? { employee_id: payload.employee_id } : {}),
      };
      const res = await apiRequest<LeaveIntakeResponse>(
        `${API_BASE_URL}/api/v1/leave/intake`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(body),
        },
        "Leave intake assistant failed"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; AI Leave Assistant is unavailable."
      );
    },
  });
}

export type LeaveRequestSubmitInput = Omit<LeaveRequestCreateRequest, "start_workflow"> & {
  start_workflow?: boolean;
};

export function useSubmitLeaveRequest() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<LeaveRequestResponse, Error, LeaveRequestSubmitInput>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      // Omit empty optional strings so FastAPI strict schemas never fail 422
      const body: Record<string, unknown> = {
        start_date: payload.start_date,
        end_date: payload.end_date,
        is_half_day: Boolean(payload.is_half_day),
        reason: payload.reason.trim(),
      };
      if (payload.leave_type_id && payload.leave_type_id.trim()) {
        body.leave_type_id = payload.leave_type_id.trim();
      }
      if (payload.leave_type_code && payload.leave_type_code.trim()) {
        body.leave_type_code = payload.leave_type_code.trim();
      }
      if (payload.is_half_day && payload.half_day_period) {
        body.half_day_period = payload.half_day_period;
      }
      if (payload.document_id && payload.document_id.trim()) {
        body.document_id = payload.document_id.trim();
      }
      if (payload.employee_id && payload.employee_id.trim()) {
        body.employee_id = payload.employee_id.trim();
      }
      if (payload.as_of_date) {
        body.as_of_date = payload.as_of_date;
      }
      if (payload.start_workflow !== undefined) {
        body.start_workflow = payload.start_workflow;
      }

      const res = await apiRequest<LeaveRequestResponse>(
        `${API_BASE_URL}/api/v1/leave/requests`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(body),
        },
        "Failed to submit leave request"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; leave request could not be submitted."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave"] });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}

export function useDecideLeaveRequest() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    LeaveRequestResponse,
    Error,
    { requestId: string; payload: LeaveDecisionRequest }
  >({
    mutationFn: async ({ requestId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const body: LeaveDecisionRequest = {
        decision: payload.decision,
        ...(payload.note && payload.note.trim() ? { note: payload.note.trim() } : {}),
      };
      const res = await apiRequest<LeaveRequestResponse>(
        `${API_BASE_URL}/api/v1/leave/requests/${requestId}/decide`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(body),
        },
        "Failed to decide leave request"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; decision could not be recorded."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave"] });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["attendance"] });
    },
  });
}

export function useCancelLeaveRequest() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    LeaveRequestResponse,
    Error,
    { requestId: string; payload?: LeaveCancelRequest }
  >({
    mutationFn: async ({ requestId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const body: LeaveCancelRequest =
        payload?.reason && payload.reason.trim() ? { reason: payload.reason.trim() } : {};
      const res = await apiRequest<LeaveRequestResponse>(
        `${API_BASE_URL}/api/v1/leave/requests/${requestId}/cancel`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(body),
        },
        "Failed to cancel leave request"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; leave request could not be cancelled."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leave"] });
      queryClient.invalidateQueries({ queryKey: ["attendance"] });
    },
  });
}
