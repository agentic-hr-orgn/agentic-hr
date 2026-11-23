"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiHttpError, apiRequest } from "@/lib/api-request";
import { getAuthHeader, isAuthQueryReady } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

export interface ApprovalRevisionItem {
  id: string;
  approval_id: string;
  revision_number: number;
  payload: Record<string, any>;
  submitted_by: string | null;
  submitter_name: string | null;
  submitted_at: string;
  decision: string | null;
  decided_by: string | null;
  decider_name: string | null;
  decision_reason: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface ApprovalItem {
  id: string;
  tenant_id: string;
  approval_type: string;
  current_revision: number;
  payload: Record<string, any>;
  requester_id: string | null;
  requester_name: string | null;
  requester_email: string | null;
  approver_role: string;
  status: "pending" | "changes_requested" | "approved" | "rejected" | string;
  decided_by: string | null;
  decider_name: string | null;
  decision_reason: string | null;
  created_at: string;
  decided_at: string | null;
  resubmitted_at: string | null;
  revisions: ApprovalRevisionItem[];
}

export interface CreateApprovalInput {
  approval_type: "leave_request" | "candidate_offer" | "payroll_override" | "policy_exception";
  payload: Record<string, any>;
  approver_role: "owner" | "manager" | "hr_head" | "payroll_admin";
}

function buildHeaders(authHeader: string | null): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

export function useApprovals(statusFilter?: string) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<ApprovalItem[], Error>({
    queryKey: ["approvals", orgId, statusFilter],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const url = new URL(`${API_BASE_URL}/api/v1/approvals`);
      if (statusFilter && statusFilter !== "all") {
        url.searchParams.set("status", statusFilter);
      }

      const res = await apiRequest<ApprovalItem[]>(
        url.toString(),
        { headers: buildHeaders(authHeader) },
        "Failed to fetch approvals"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("OFFLINE: FastAPI backend is currently unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn),
    staleTime: 1000 * 30,
    retry: 1,
  });
}

export function useApprovalDetails(id: string | null) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<ApprovalItem, Error>({
    queryKey: ["approval", id, orgId],
    queryFn: async () => {
      if (!id) throw new Error("Approval ID required");
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);

      const res = await apiRequest<ApprovalItem>(
        `${API_BASE_URL}/api/v1/approvals/${id}`,
        { headers: buildHeaders(authHeader) },
        "Failed to fetch approval details"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("OFFLINE: FastAPI backend is currently unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn) && !!id,
    retry: 1,
  });
}

export function useApprovalRevisions(id: string | null) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<ApprovalRevisionItem[], Error>({
    queryKey: ["approval-revisions", id, orgId],
    queryFn: async () => {
      if (!id) return [];
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);

      const res = await apiRequest<ApprovalRevisionItem[]>(
        `${API_BASE_URL}/api/v1/approvals/${id}/revisions`,
        { headers: buildHeaders(authHeader) },
        "Failed to fetch revisions"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("OFFLINE: FastAPI backend is currently unreachable.");
    },
    enabled: isAuthQueryReady(isLoaded, isSignedIn) && !!id,
    retry: 1,
  });
}

export function useApproveMutation() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason?: string }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ApprovalItem>(
        `${API_BASE_URL}/api/v1/approvals/${id}/approve`,
        {
          method: "POST",
          headers: buildHeaders(authHeader),
          body: JSON.stringify({ reason: reason || null }),
        },
        "Approval failed"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("FastAPI backend is offline; approval decision could not be recorded.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["approval"] });
      queryClient.invalidateQueries({ queryKey: ["approval-revisions"] });
    },
  });
}

export function useRequestChangesMutation() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason: string }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ApprovalItem>(
        `${API_BASE_URL}/api/v1/approvals/${id}/request-changes`,
        {
          method: "POST",
          headers: buildHeaders(authHeader),
          body: JSON.stringify({ reason }),
        },
        "Requesting changes failed"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("FastAPI backend is offline; change request could not be submitted.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["approval"] });
      queryClient.invalidateQueries({ queryKey: ["approval-revisions"] });
    },
  });
}

export function useRejectMutation() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason: string }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ApprovalItem>(
        `${API_BASE_URL}/api/v1/approvals/${id}/reject`,
        {
          method: "POST",
          headers: buildHeaders(authHeader),
          body: JSON.stringify({ reason }),
        },
        "Rejection failed"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("FastAPI backend is offline; rejection could not be recorded.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["approval"] });
      queryClient.invalidateQueries({ queryKey: ["approval-revisions"] });
    },
  });
}

export function useResubmitMutation() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, payload }: { id: string; payload: Record<string, unknown> }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ApprovalItem>(
        `${API_BASE_URL}/api/v1/approvals/${id}/resubmit`,
        {
          method: "POST",
          headers: buildHeaders(authHeader),
          body: JSON.stringify({ payload }),
        },
        "Resubmission failed"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("FastAPI backend is offline; resubmission could not be completed.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["approval"] });
      queryClient.invalidateQueries({ queryKey: ["approval-revisions"] });
    },
  });
}

export function useCreateApprovalMutation() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: CreateApprovalInput) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ApprovalItem>(
        `${API_BASE_URL}/api/v1/approvals`,
        {
          method: "POST",
          headers: buildHeaders(authHeader),
          body: JSON.stringify(input),
        },
        "Approval creation failed"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("FastAPI backend is offline; approval request could not be created.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}
