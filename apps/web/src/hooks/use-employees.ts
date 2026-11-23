"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  EmployeeCreateRequest,
  EmployeeDocument,
  EmployeeListResponse,
  EmployeeProfile,
  EmployeeUpdateRequest,
  OrgChartResponse,
} from "@agentic-hr/contracts";
import {
  ApiHttpError,
  ApiOfflineError,
  apiDownloadBlob,
  apiRequest,
  triggerBrowserBlobDownload,
} from "@/lib/api-request";
import { getAuthHeader, isAuthQueryReady } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

export interface EmployeeListParams {
  search?: string;
  departmentId?: string;
  status?: string;
  employmentType?: string;
  page?: number;
  pageSize?: number;
  enabled?: boolean;
}

function buildHeaders(authHeader: string | null, json = false): Record<string, string> {
  const headers: Record<string, string> = {};
  if (json) {
    headers["Content-Type"] = "application/json";
  }
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

export function useEmployees(params: EmployeeListParams = {}) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const {
    search = "",
    departmentId = "",
    status = "",
    employmentType = "",
    page = 1,
    pageSize = 15,
    enabled = true,
  } = params;

  return useQuery<EmployeeListResponse, Error>({
    queryKey: [
      "employees",
      orgId,
      { search, departmentId, status, employmentType, page, pageSize },
    ],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const url = new URL(`${API_BASE_URL}/api/v1/employees`);
      if (search.trim()) url.searchParams.set("search", search.trim());
      if (departmentId) url.searchParams.set("department_id", departmentId);
      if (status) url.searchParams.set("status", status);
      if (employmentType) url.searchParams.set("employment_type", employmentType);
      url.searchParams.set("page", String(page));
      url.searchParams.set("page_size", String(pageSize));

      const res = await apiRequest<EmployeeListResponse>(
        url.toString(),
        { headers: buildHeaders(authHeader, true) },
        "Failed to load employees"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn) && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useEmployeeProfile(employeeId: string | null) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<EmployeeProfile, Error>({
    queryKey: ["employees", "profile", orgId, employeeId],
    queryFn: async () => {
      if (!employeeId) throw new Error("Employee ID required");
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<EmployeeProfile>(
        `${API_BASE_URL}/api/v1/employees/${employeeId}`,
        { headers: buildHeaders(authHeader, true) },
        "Failed to load employee profile"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn) && Boolean(employeeId),
    staleTime: 1000 * 15,
    retry: false,
  });
}

export function useCreateEmployee() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<EmployeeProfile, Error, EmployeeCreateRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<EmployeeProfile>(
        `${API_BASE_URL}/api/v1/employees`,
        {
          method: "POST",
          headers: buildHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to create employee"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; employee could not be created."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["employees"] });
      queryClient.invalidateQueries({ queryKey: ["employees", "org-chart"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}

export function useUpdateEmployee() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    EmployeeProfile,
    Error,
    { employeeId: string; payload: EmployeeUpdateRequest }
  >({
    mutationFn: async ({ employeeId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<EmployeeProfile>(
        `${API_BASE_URL}/api/v1/employees/${employeeId}`,
        {
          method: "PATCH",
          headers: buildHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to update employee"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; employee updates could not be saved."
      );
    },
    onSuccess: (_updated, variables) => {
      queryClient.invalidateQueries({ queryKey: ["employees"] });
      queryClient.invalidateQueries({ queryKey: ["employees", "profile"] });
      queryClient.invalidateQueries({
        queryKey: ["employees", "profile", variables.employeeId],
      });
      queryClient.invalidateQueries({ queryKey: ["employees", "org-chart"] });
    },
  });
}

export function useOrgChart(enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<OrgChartResponse, Error>({
    queryKey: ["employees", "org-chart", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<OrgChartResponse>(
        `${API_BASE_URL}/api/v1/employees/org-chart`,
        { headers: buildHeaders(authHeader, true) },
        "Failed to load organization chart"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn) && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useEmployeeDocuments(employeeId: string | null) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<EmployeeDocument[], Error>({
    queryKey: ["employees", "documents", orgId, employeeId],
    queryFn: async () => {
      if (!employeeId) throw new Error("Employee ID required");
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<EmployeeDocument[]>(
        `${API_BASE_URL}/api/v1/employees/${employeeId}/documents`,
        { headers: buildHeaders(authHeader, true) },
        "Failed to load employee documents"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn) && Boolean(employeeId),
    staleTime: 1000 * 15,
    retry: false,
  });
}

export interface UploadEmployeeDocumentInput {
  file: File;
  documentType: "cnic" | "contract" | "certificate" | "other";
  title?: string;
  expiryDate?: string;
}

export function useUploadEmployeeDocument(employeeId: string) {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<EmployeeDocument, Error, UploadEmployeeDocumentInput>({
    mutationFn: async ({ file, documentType, title, expiryDate }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const formData = new FormData();
      formData.append("file", file);
      formData.append("document_type", documentType);
      if (title && title.trim()) {
        formData.append("title", title.trim());
      }
      if (expiryDate && expiryDate.trim()) {
        formData.append("expiry_date", expiryDate.trim());
      }

      const res = await apiRequest<EmployeeDocument>(
        `${API_BASE_URL}/api/v1/employees/${employeeId}/documents`,
        {
          method: "POST",
          headers: buildHeaders(authHeader, false),
          body: formData,
        },
        "Failed to upload employee document"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; document upload cannot proceed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["employees", "documents"] });
    },
  });
}

export function useDownloadEmployeeDocument() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    { filename: string },
    Error,
    { employeeId: string; documentId: string; fileName?: string }
  >({
    mutationFn: async ({ employeeId, documentId, fileName }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiDownloadBlob(
        `${API_BASE_URL}/api/v1/employees/${employeeId}/documents/${documentId}/download`,
        { headers: buildHeaders(authHeader, false) },
        "Failed to download employee document"
      );

      if (res.kind === "live") {
        const resolvedName = res.data.filename || fileName || "employee_document";
        triggerBrowserBlobDownload(res.data.blob, resolvedName);
        return { filename: resolvedName };
      }
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; document could not be downloaded."
      );
    },
  });
}

export function useDeleteEmployeeDocument(employeeId: string) {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<{ status: string; id: string }, Error, string>({
    mutationFn: async (documentId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<{ status: string; id: string }>(
        `${API_BASE_URL}/api/v1/employees/${employeeId}/documents/${documentId}`,
        {
          method: "DELETE",
          headers: buildHeaders(authHeader, true),
        },
        "Failed to delete employee document"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; document could not be deleted."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["employees", "documents"] });
    },
  });
}
