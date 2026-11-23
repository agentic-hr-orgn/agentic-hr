"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  PolicyArchiveResponse,
  PolicyChunkPreviewItem,
  PolicyChunkUpdateRequest,
  PolicyDocumentItem,
  PolicyPageFixRequest,
  PolicyPreviewResponse,
  PolicyPublishRequest,
  PolicyPublishResponse,
  PolicyReviewQueueItem,
  PolicySearchRequest,
  PolicySearchResponse,
} from "@agentic-hr/contracts";
import { ApiHttpError, ApiOfflineError, apiRequest } from "@/lib/api-request";
import { getAuthHeader, isAuthQueryReady } from "@/lib/auth-header";
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

export interface PolicyListFilters {
  docType?: string;
  visibility?: string;
  lifecycleStatus?: string;
  ingestionStatus?: string;
  language?: string;
  needsReviewOnly?: boolean;
  includeArchived?: boolean;
}

function buildPoliciesQueryString(filters?: PolicyListFilters): string {
  if (!filters) return "";
  const params = new URLSearchParams();
  if (filters.docType) {
    params.set("doc_type", filters.docType);
  }
  if (filters.visibility) {
    params.set("visibility", filters.visibility);
  }
  if (filters.lifecycleStatus) {
    params.set("lifecycle_status", filters.lifecycleStatus);
  }
  if (filters.ingestionStatus) {
    params.set("ingestion_status", filters.ingestionStatus);
  }
  if (filters.language) {
    params.set("language", filters.language);
  }
  if (filters.needsReviewOnly) {
    params.set("needs_review_only", "true");
  }
  if (filters.includeArchived) {
    params.set("include_archived", "true");
  }
  return params.toString();
}

export function usePolicies(filters?: PolicyListFilters) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const queryString = buildPoliciesQueryString(filters);

  return useQuery<PolicyDocumentItem[], Error>({
    queryKey: ["policies", orgId, filters ?? {}],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const url = queryString
        ? `${API_BASE_URL}/api/v1/policies?${queryString}`
        : `${API_BASE_URL}/api/v1/policies`;
      const res = await apiRequest<PolicyDocumentItem[]>(
        url,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load policies"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn),
    staleTime: 1000 * 30,
    retry: false,
    refetchInterval: (query) => {
      if (query.state.error) return false;
      const items = query.state.data;
      if (!Array.isArray(items)) return false;
      const hasInFlight = items.some(
        (doc) => doc.ingestion_status === "queued" || doc.ingestion_status === "processing"
      );
      return hasInFlight ? 3000 : false;
    },
  });
}

export interface UploadPolicyInput {
  file: File;
  title?: string;
  docType?: "company_policy" | "labour_law" | "helpdesk_knowledge" | string;
  visibility?: "all" | "managers" | "hr_only" | string;
  language?: "en" | "ur" | "mixed" | string;
  effectiveDate?: string;
  source?: string;
  storageProvider?: "local" | "google_drive";
}

export function useUploadPolicy() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<PolicyDocumentItem, Error, UploadPolicyInput>({
    mutationFn: async ({
      file,
      title,
      docType,
      visibility,
      language,
      effectiveDate,
      source,
      storageProvider = "local",
    }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const formData = new FormData();
      formData.append("file", file);

      // FastAPI Form(...) optional strings/dates fail 422 if sent as empty strings.
      if (title && title.trim()) {
        formData.append("title", title.trim());
      }
      if (docType && docType.trim()) {
        formData.append("doc_type", docType.trim());
      }
      if (visibility && visibility.trim()) {
        formData.append("visibility", visibility.trim());
      }
      if (language && language.trim() && language.trim().toLowerCase() !== "auto") {
        formData.append("language", language.trim());
      }
      if (effectiveDate && effectiveDate.trim()) {
        formData.append("effective_date", effectiveDate.trim());
      }
      if (source && source.trim()) {
        formData.append("source", source.trim());
      }
      formData.append("storage_provider", storageProvider);

      const res = await apiRequest<PolicyDocumentItem>(
        `${API_BASE_URL}/api/v1/policies`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, false),
          body: formData,
        },
        "Failed to upload policy document"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; policy upload cannot proceed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policies"] });
      queryClient.invalidateQueries({ queryKey: ["policies", "review-queue"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}

export function useDeletePolicy() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<PolicyArchiveResponse, Error, string>({
    mutationFn: async (policyId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<PolicyArchiveResponse>(
        `${API_BASE_URL}/api/v1/policies/${policyId}`,
        {
          method: "DELETE",
          headers: buildAuthHeaders(authHeader, true),
        },
        "Failed to archive policy document"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; policy could not be archived."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policies"] });
      queryClient.invalidateQueries({ queryKey: ["policies", "review-queue"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}

export function usePolicyReviewQueue(enabled = true) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<PolicyReviewQueueItem[], Error>({
    queryKey: ["policies", "review-queue", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<PolicyReviewQueueItem[]>(
        `${API_BASE_URL}/api/v1/policies/review-queue`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load policy review queue"
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

export function usePolicyPreview(policyId: string | null | undefined) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<PolicyPreviewResponse, Error>({
    queryKey: ["policies", "preview", orgId, policyId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<PolicyPreviewResponse>(
        `${API_BASE_URL}/api/v1/policies/${policyId}/preview`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load policy preview"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn) && Boolean(policyId),
    retry: false,
    refetchInterval: (query) => {
      if (query.state.error) return false;
      const status = query.state.data?.document?.ingestion_status;
      return status === "queued" || status === "processing" ? 3000 : false;
    },
  });
}

export function useFixPolicyChunk(policyId: string) {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    PolicyChunkPreviewItem,
    Error,
    { chunkId: string; payload: PolicyChunkUpdateRequest }
  >({
    mutationFn: async ({ chunkId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<PolicyChunkPreviewItem>(
        `${API_BASE_URL}/api/v1/policies/${policyId}/chunks/${chunkId}`,
        {
          method: "PATCH",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to update policy chunk"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is offline; chunk edit failed.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policies"] });
      queryClient.invalidateQueries({ queryKey: ["policies", "preview"] });
    },
  });
}

export function useFixPolicyPage(policyId: string) {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    PolicyDocumentItem,
    Error,
    { pageNumber: number; payload: PolicyPageFixRequest }
  >({
    mutationFn: async ({ pageNumber, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<PolicyDocumentItem>(
        `${API_BASE_URL}/api/v1/policies/${policyId}/pages/${pageNumber}/fix`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(payload),
        },
        "Failed to save policy page fix"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is offline; page fix failed.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policies"] });
      queryClient.invalidateQueries({ queryKey: ["policies", "preview"] });
      queryClient.invalidateQueries({ queryKey: ["policies", "review-queue"] });
    },
  });
}

export function usePublishPolicy() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    PolicyPublishResponse,
    Error,
    { policyId: string; payload?: Partial<PolicyPublishRequest> }
  >({
    mutationFn: async ({ policyId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const body: PolicyPublishRequest = {
        approver_role: payload?.approver_role ?? "owner",
      };
      const res = await apiRequest<PolicyPublishResponse>(
        `${API_BASE_URL}/api/v1/policies/${policyId}/publish`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(body),
        },
        "Failed to submit policy for publish approval"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; publish approval request failed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policies"] });
      queryClient.invalidateQueries({ queryKey: ["policies", "preview"] });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}

export function useReingestPolicy() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<PolicyDocumentItem, Error, string>({
    mutationFn: async (policyId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<PolicyDocumentItem>(
        `${API_BASE_URL}/api/v1/policies/${policyId}/reingest`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
        },
        "Failed to trigger policy re-ingestion"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; re-ingestion cannot be started."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policies"] });
      queryClient.invalidateQueries({ queryKey: ["policies", "preview"] });
      queryClient.invalidateQueries({ queryKey: ["policies", "review-queue"] });
    },
  });
}

export function useSearchPolicies() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<PolicySearchResponse, Error, PolicySearchRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const body: PolicySearchRequest = {
        query: payload.query.trim(),
        top_k: payload.top_k ?? 5,
        ...(payload.doc_type ? { doc_type: payload.doc_type } : {}),
        ...(payload.language ? { language: payload.language } : {}),
        ...(payload.document_id ? { document_id: payload.document_id } : {}),
        ...(payload.effective_as_of ? { effective_as_of: payload.effective_as_of } : {}),
      };
      const res = await apiRequest<PolicySearchResponse>(
        `${API_BASE_URL}/api/v1/policies/search`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, true),
          body: JSON.stringify(body),
        },
        "Policy search failed"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; policy search is unavailable."
      );
    },
  });
}
