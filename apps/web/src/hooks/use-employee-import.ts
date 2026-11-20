"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ImportCommitResponse, ImportValidationResult } from "@agentic-hr/contracts";
import {
  ApiHttpError,
  ApiOfflineError,
  apiDownloadBlob,
  apiRequest,
  triggerBrowserBlobDownload,
} from "@/lib/api-request";
import { getAuthHeader } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

export { triggerBrowserBlobDownload };

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

export function useDownloadImportTemplate() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<{ filename: string }, Error, void>({
    mutationFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiDownloadBlob(
        `${API_BASE_URL}/api/v1/employees/import/template`,
        { headers: buildAuthHeaders(authHeader, false) },
        "Failed to download Excel template"
      );

      if (res.kind === "live") {
        const fname = res.data.filename || "employee_import_template.xlsx";
        triggerBrowserBlobDownload(res.data.blob, fname);
        return { filename: fname };
      }
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; Excel template could not be downloaded."
      );
    },
  });
}

export function useValidateEmployeeImport() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ImportValidationResult, Error, File>({
    mutationFn: async (file) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const formData = new FormData();
      formData.append("file", file);

      const res = await apiRequest<ImportValidationResult>(
        `${API_BASE_URL}/api/v1/employees/import/validate`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, false),
          body: formData,
        },
        "Failed to validate employee spreadsheet"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; spreadsheet validation cannot run."
      );
    },
  });
}

export function useDownloadImportErrorReport() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<{ filename: string }, Error, string>({
    mutationFn: async (batchId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiDownloadBlob(
        `${API_BASE_URL}/api/v1/employees/import/${batchId}/error-report`,
        { headers: buildAuthHeaders(authHeader, false) },
        "Failed to download Excel error report"
      );

      if (res.kind === "live") {
        const safeBatch = batchId.replace(/[^a-zA-Z0-9]/g, "").slice(0, 24) || "batch";
        const fname = res.data.filename || `employee_import_errors_${safeBatch}.xlsx`;
        triggerBrowserBlobDownload(res.data.blob, fname);
        return { filename: fname };
      }
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; error report could not be downloaded."
      );
    },
  });
}

export function useCommitEmployeeImport() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ImportCommitResponse, Error, string>({
    mutationFn: async (batchId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ImportCommitResponse>(
        `${API_BASE_URL}/api/v1/employees/import/${batchId}/commit`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
        },
        "Failed to commit imported employees"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; employee batch could not be committed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
      queryClient.invalidateQueries({ queryKey: ["employees"] });
    },
  });
}
