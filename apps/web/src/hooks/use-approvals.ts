"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

async function getAuthHeader(
  getToken: () => Promise<string | null>,
  isLoaded: boolean,
  isSignedIn: boolean | undefined
): Promise<string> {
  let token: string | null = null;
  if (isLoaded && isSignedIn) {
    try {
      token = await getToken();
    } catch {
      // Fallback
    }
  }
  return token
    ? `Bearer ${token}`
    : "Bearer mock:agentic.hr.test.ubaid.owner@gmail.com:org_acme_tech_01";
}

export function useApprovals(statusFilter?: string) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useQuery<ApprovalItem[], Error>({
    queryKey: ["approvals", orgId, statusFilter],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const url = new URL(`${apiUrl}/api/v1/approvals`);
      if (statusFilter && statusFilter !== "all") {
        url.searchParams.set("status", statusFilter);
      }

      const res = await fetch(url.toString(), {
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to fetch approvals: ${res.status}`);
      }

      return (await res.json()) as ApprovalItem[];
    },
    staleTime: 1000 * 30, // 30 seconds
  });
}

export function useApprovalDetails(id: string | null) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useQuery<ApprovalItem, Error>({
    queryKey: ["approval", id, orgId],
    queryFn: async () => {
      if (!id) throw new Error("Approval ID required");
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);

      const res = await fetch(`${apiUrl}/api/v1/approvals/${id}`, {
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to fetch approval details: ${res.status}`);
      }

      return (await res.json()) as ApprovalItem;
    },
    enabled: !!id,
  });
}

export function useApprovalRevisions(id: string | null) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useQuery<ApprovalRevisionItem[], Error>({
    queryKey: ["approval-revisions", id, orgId],
    queryFn: async () => {
      if (!id) return [];
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);

      const res = await fetch(`${apiUrl}/api/v1/approvals/${id}/revisions`, {
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to fetch revisions: ${res.status}`);
      }

      return (await res.json()) as ApprovalRevisionItem[];
    },
    enabled: !!id,
  });
}

export function useApproveMutation() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const queryClient = useQueryClient();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason?: string }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/approvals/${id}/approve`, {
        method: "POST",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ reason: reason || null }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Approval failed with status ${res.status}`);
      }

      return (await res.json()) as ApprovalItem;
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
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason: string }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/approvals/${id}/request-changes`, {
        method: "POST",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ reason }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Requesting changes failed with status ${res.status}`);
      }

      return (await res.json()) as ApprovalItem;
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
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason: string }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/approvals/${id}/reject`, {
        method: "POST",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ reason }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Rejection failed with status ${res.status}`);
      }

      return (await res.json()) as ApprovalItem;
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
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation({
    mutationFn: async ({ id, payload }: { id: string; payload: Record<string, any> }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/approvals/${id}/resubmit`, {
        method: "POST",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ payload }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Resubmission failed with status ${res.status}`);
      }

      return (await res.json()) as ApprovalItem;
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
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation({
    mutationFn: async (input: CreateApprovalInput) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/approvals`, {
        method: "POST",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(input),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Creation failed with status ${res.status}`);
      }

      return (await res.json()) as ApprovalItem;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}
