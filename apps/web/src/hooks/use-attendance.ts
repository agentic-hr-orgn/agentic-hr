"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  AttendanceAutoFillRequest,
  AttendanceAutoFillResponse,
  AttendanceCheckInRequest,
  AttendanceImportCommitResponse,
  AttendanceImportValidationResult,
  AttendanceMonthLockResponse,
  AttendanceMonthReviewResponse,
  AttendancePostLockEditRequest,
  AttendancePostLockEditResponse,
  AttendanceRecordResponse,
  AttendanceTodayResponse,
  DailyAttendanceGridResponse,
  DailyAttendanceUpsertRequest,
  DailyAttendanceUpsertResponse,
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

export function useMyTodayAttendance(enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<AttendanceTodayResponse, Error>({
    queryKey: ["attendance", "me-today", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<AttendanceTodayResponse>(
        `${API_BASE_URL}/api/v1/attendance/me/today`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load today's attendance status"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useCheckIn() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<AttendanceRecordResponse, Error, AttendanceCheckInRequest | undefined>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const body: Record<string, unknown> = {};
      if (payload?.action) body.action = payload.action;
      if (payload?.check_out !== undefined) body.check_out = payload.check_out;
      if (payload?.notes && payload.notes.trim()) body.notes = payload.notes.trim();
      if (payload?.target_date) body.target_date = payload.target_date;

      const res = await apiRequest<AttendanceRecordResponse>(
        `${API_BASE_URL}/api/v1/attendance/check-in`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(body),
        },
        "Check-in request failed"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; check-in could not be saved."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["attendance"] });
    },
  });
}

export function useDailyAttendanceGrid(targetDate: string, departmentId?: string, enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<DailyAttendanceGridResponse, Error>({
    queryKey: ["attendance", "daily", orgId, targetDate, departmentId ?? "all"],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const sp = new URLSearchParams({ target_date: targetDate });
      if (departmentId) sp.set("department_id", departmentId);
      const res = await apiRequest<DailyAttendanceGridResponse>(
        `${API_BASE_URL}/api/v1/attendance/daily?${sp.toString()}`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load daily attendance grid"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled && Boolean(targetDate),
    staleTime: 1000 * 20,
    retry: false,
  });
}

export function useUpsertDailyAttendanceGrid() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<DailyAttendanceUpsertResponse, Error, DailyAttendanceUpsertRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<DailyAttendanceUpsertResponse>(
        `${API_BASE_URL}/api/v1/attendance/daily`,
        {
          method: "PUT",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to save daily attendance grid"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; daily attendance could not be saved."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["attendance"] });
    },
  });
}

export function useValidateAttendanceImport() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<AttendanceImportValidationResult, Error, File>({
    mutationFn: async (file) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const formData = new FormData();
      formData.append("file", file);
      const res = await apiRequest<AttendanceImportValidationResult>(
        `${API_BASE_URL}/api/v1/attendance/import/validate`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, false),
          body: formData,
        },
        "Attendance Excel validation failed"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; Excel validation is unavailable."
      );
    },
  });
}

export function useCommitAttendanceImport() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<AttendanceImportCommitResponse, Error, string>({
    mutationFn: async (batchId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<AttendanceImportCommitResponse>(
        `${API_BASE_URL}/api/v1/attendance/import/${batchId}/commit`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
        },
        "Failed to commit attendance import batch"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; attendance import commit failed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["attendance"] });
    },
  });
}

export function useAutoFillMonthAttendance() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    AttendanceAutoFillResponse,
    Error,
    { yearMonth: string; payload?: AttendanceAutoFillRequest }
  >({
    mutationFn: async ({ yearMonth, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<AttendanceAutoFillResponse>(
        `${API_BASE_URL}/api/v1/attendance/months/${yearMonth}/auto-fill`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload ?? { overwrite_existing: false }),
        },
        "Failed to auto-fill attendance month"
      );
      return unwrapApiResult(res, "OFFLINE: FastAPI backend is offline; auto-fill could not run.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["attendance"] });
    },
  });
}

export function useMonthAttendanceReview(yearMonth: string, enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<AttendanceMonthReviewResponse, Error>({
    queryKey: ["attendance", "review", orgId, yearMonth],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<AttendanceMonthReviewResponse>(
        `${API_BASE_URL}/api/v1/attendance/months/${yearMonth}/review`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load attendance month review"
      );
      return unwrapApiResult(res);
    },
    enabled: isLoaded && enabled && Boolean(yearMonth),
    staleTime: 1000 * 15,
    retry: false,
  });
}

export function useLockMonthAttendance() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<AttendanceMonthLockResponse, Error, string>({
    mutationFn: async (yearMonth) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<AttendanceMonthLockResponse>(
        `${API_BASE_URL}/api/v1/attendance/months/${yearMonth}/lock`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
        },
        "Failed to lock attendance month"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; attendance month could not be locked."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["attendance"] });
    },
  });
}

export type AttendancePostLockEditInput = Omit<AttendancePostLockEditRequest, "approver_role"> & {
  approver_role?: "owner" | "hr_head";
};

export function useRequestPostLockEdit() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<AttendancePostLockEditResponse, Error, AttendancePostLockEditInput>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const body: AttendancePostLockEditRequest = {
        employee_id: payload.employee_id,
        attendance_date: payload.attendance_date,
        new_status: payload.new_status,
        reason: payload.reason.trim(),
        approver_role: payload.approver_role ?? "hr_head",
        ...(payload.notes && payload.notes.trim() ? { notes: payload.notes.trim() } : {}),
      };
      const res = await apiRequest<AttendancePostLockEditResponse>(
        `${API_BASE_URL}/api/v1/attendance/post-lock-edit`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(body),
        },
        "Failed to submit post-lock attendance edit request"
      );
      return unwrapApiResult(
        res,
        "OFFLINE: FastAPI backend is offline; post-lock edit request failed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["attendance"] });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}
