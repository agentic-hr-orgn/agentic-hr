"use client";

import React, { useId, useRef, useState } from "react";
import Link from "next/link";
import {
  Archive,
  ArrowLeft,
  BookOpenCheck,
  CheckCircle2,
  ExternalLink,
  FileText,
  Languages,
  RefreshCw,
  Send,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import type { PolicyDocumentItem } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { PolicyReviewQueue } from "@/components/policies/policy-review-queue";
import { PolicySearchPanel } from "@/components/policies/policy-search-panel";
import {
  IngestionStatusBadge,
  LifecycleStatusBadge,
  VisibilityBadge,
  canManagePolicies,
  formatBytes,
  formatDocType,
} from "@/components/policies/policy-status-badges";
import { PolicyUploadForm } from "@/components/policies/policy-upload-form";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useHydrated } from "@/hooks/use-hydrated";
import {
  useDeletePolicy,
  usePolicies,
  usePolicyReviewQueue,
  usePublishPolicy,
  useReingestPolicy,
} from "@/hooks/use-policies";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";
import { useToastStore } from "@/stores/toast-store";

type PolicyWorkspaceTab = "library" | "search" | "review";

export default function PoliciesPage() {
  const tableCaptionId = useId();
  const filterTypeId = useId();
  const filterVisibilityId = useId();
  const filterStatusId = useId();
  const filterReviewId = useId();
  const filterArchivedId = useId();

  const tabLibraryId = useId();
  const tabSearchId = useId();
  const tabReviewId = useId();

  const tabRefs = useRef<Partial<Record<PolicyWorkspaceTab, HTMLButtonElement | null>>>({});

  // Keep SSR and initial client hydration deterministic even if Header/Sidebar
  // populated React Query cache (e.g. /api/v1/auth/me) before this Suspense chunk hydrated.
  const isHydrated = useHydrated();

  const { data: currentUser } = useCurrentUser();
  const canManage = isHydrated && canManagePolicies(currentUser);
  const userRole = isHydrated ? currentUser?.role?.toLowerCase() || "employee" : "employee";

  const [activeTab, setActiveTab] = useState<PolicyWorkspaceTab>("library");
  const [docTypeFilter, setDocTypeFilter] = useState("");
  const [visibilityFilter, setVisibilityFilter] = useState("");
  const [lifecycleFilter, setLifecycleFilter] = useState("");
  const [needsReviewOnly, setNeedsReviewOnly] = useState(false);
  const [includeArchived, setIncludeArchived] = useState(false);

  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null);
  const [pendingReingestId, setPendingReingestId] = useState<string | null>(null);
  const [pendingArchiveId, setPendingArchiveId] = useState<string | null>(null);

  const {
    isStandby: isBackendStandby,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();

  const {
    data: policies = [],
    isLoading: isPoliciesLoading,
    error: policiesError,
    refetch: refetchPolicies,
    isFetching: isPoliciesFetching,
  } = usePolicies({
    docType: docTypeFilter,
    visibility: visibilityFilter,
    lifecycleStatus: lifecycleFilter,
    needsReviewOnly,
    includeArchived,
  });

  // Gate review queue strictly on Owner / HR Head so plain employees/managers never trigger 403
  const {
    data: reviewQueue = [],
    isLoading: isReviewQueueLoading,
    error: reviewQueueError,
    refetch: refetchReviewQueue,
  } = usePolicyReviewQueue(canManage);

  const publishPolicy = usePublishPolicy();
  const reingestPolicy = useReingestPolicy();
  const deletePolicy = useDeletePolicy();
  const pushToast = useToastStore((s) => s.pushToast);

  const effectivePolicies = isHydrated ? policies : [];
  const effectivePoliciesLoading = isHydrated ? isPoliciesLoading : false;
  const effectiveReviewQueue = isHydrated ? reviewQueue : [];

  const isOffline =
    isHydrated &&
    (isBackendStandby || isOfflineError(policiesError) || isOfflineError(reviewQueueError));
  const liveHttpError =
    isHydrated && policiesError && !isOfflineError(policiesError) ? policiesError.message : null;
  const displayedError = actionError || liveHttpError;

  const visibleTabs: Array<{ id: PolicyWorkspaceTab; domId: string; label: string }> = canManage
    ? [
        { id: "library", domId: tabLibraryId, label: "Policy Library" },
        { id: "search", domId: tabSearchId, label: "Bilingual Search" },
        { id: "review", domId: tabReviewId, label: "OCR & Review Queue" },
      ]
    : [
        { id: "library", domId: tabLibraryId, label: "Policy Library" },
        { id: "search", domId: tabSearchId, label: "Bilingual Search" },
      ];

  const resolvedActiveTab: PolicyWorkspaceTab =
    activeTab === "review" && !canManage ? "library" : activeTab;

  const handleTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, idx: number) => {
    let nextIdx: number | null = null;
    if (e.key === "ArrowRight") {
      nextIdx = (idx + 1) % visibleTabs.length;
    } else if (e.key === "ArrowLeft") {
      nextIdx = (idx - 1 + visibleTabs.length) % visibleTabs.length;
    } else if (e.key === "Home") {
      nextIdx = 0;
    } else if (e.key === "End") {
      nextIdx = visibleTabs.length - 1;
    }

    if (nextIdx !== null) {
      e.preventDefault();
      const targetTab = visibleTabs[nextIdx];
      if (targetTab) {
        setActiveTab(targetTab.id);
        tabRefs.current[targetTab.id]?.focus();
      }
    }
  };

  const handlePublish = async (policy: PolicyDocumentItem) => {
    if (!canManage || publishPolicy.isPending || isOffline) return;
    setActionError(null);
    setPendingPublishId(policy.id);
    try {
      await publishPolicy.mutateAsync({
        policyId: policy.id,
        payload: { approver_role: "owner" },
      });
      pushToast({
        variant: "success",
        title: "Submitted for Publish Approval",
        description: `'${policy.title}' (v${policy.version ?? 1}) is awaiting decision in Approvals Inbox.`,
        actionLabel: "Open Approvals Inbox",
        actionHref: "/approvals",
      });
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to submit policy for publish approval.";
      setActionError(message);
      pushToast({ variant: "error", title: "Publish request failed", description: message });
    } finally {
      setPendingPublishId(null);
    }
  };

  const handleReingest = async (policyId: string, title: string) => {
    if (!canManage || reingestPolicy.isPending || isOffline) return;
    setActionError(null);
    setPendingReingestId(policyId);
    try {
      const updated = await reingestPolicy.mutateAsync(policyId);
      pushToast({
        variant: "success",
        title: "Policy re-ingestion triggered",
        description: `'${title}' status is now '${updated.ingestion_status}'.`,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to trigger re-ingestion.";
      setActionError(message);
      pushToast({ variant: "error", title: "Re-ingest failed", description: message });
    } finally {
      setPendingReingestId(null);
    }
  };

  const handleArchive = async (policyId: string, title: string) => {
    if (!canManage || deletePolicy.isPending || isOffline) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        `Archive policy '${title}'? Archived policies remain in audit logs but are excluded from active search.`
      )
    ) {
      return;
    }

    setActionError(null);
    setPendingArchiveId(policyId);
    try {
      await deletePolicy.mutateAsync(policyId);
      pushToast({
        variant: "success",
        title: "Policy archived",
        description: `'${title}' has been archived in the tenant policy vault.`,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to archive policy document.";
      setActionError(message);
      pushToast({ variant: "error", title: "Archive failed", description: message });
    } finally {
      setPendingArchiveId(null);
    }
  };

  const activeTabMeta = visibleTabs.find((t) => t.id === resolvedActiveTab) ?? visibleTabs[0];

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-appBorder pb-5">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              href="/"
              className="text-xs text-textSecondary hover:text-[#2A5BDB] flex items-center gap-1 font-medium transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              Dashboard
            </Link>
            <span className="text-textMuted text-xs">/</span>
            <span className="text-xs text-[#102C54] font-semibold">Policies</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[#102C54]">
            Corporate Policy Brain (Bilingual RAG)
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Manage versioned HR handbooks, repair low-confidence OCR pages, submit drafts for
            publish approval, and test bilingual English &amp; Urdu retrieval.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="primary">Stage 5 Hybrid RAG</Badge>
          <Badge variant="outline">pgvector + FTS + Trigram</Badge>
        </div>
      </div>

      {isOffline && (
        <StandbyBanner
          message="FastAPI backend is offline. Policy uploads, publishing, and live bilingual search require an active backend connection."
          onRetry={() => {
            refetchHealth();
            refetchPolicies();
            if (canManage) {
              refetchReviewQueue();
            }
          }}
          isRetrying={isHealthFetching || isPoliciesFetching}
        />
      )}

      {displayedError && (
        <div
          role="alert"
          className="flex items-center justify-between gap-2 rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{displayedError}</span>
          </div>
          {actionError && (
            <button
              type="button"
              onClick={() => setActionError(null)}
              className="font-bold text-rose-700 hover:text-rose-900 px-1"
              aria-label="Dismiss policy error"
            >
              &times;
            </button>
          )}
        </div>
      )}

      {/* Single-Row Accessible Workspace Tablist */}
      <div className="border-b border-appBorder bg-white rounded-t-medium px-2">
        <div
          role="tablist"
          aria-label="Policy Brain workspace sections"
          className="flex overflow-x-auto gap-1"
        >
          {visibleTabs.map((tab, index) => {
            const isSelected = resolvedActiveTab === tab.id;
            return (
              <button
                key={tab.id}
                ref={(el) => {
                  tabRefs.current[tab.id] = el;
                }}
                id={tab.domId}
                type="button"
                role="tab"
                aria-selected={isSelected}
                tabIndex={isSelected ? 0 : -1}
                onClick={() => setActiveTab(tab.id)}
                onKeyDown={(e) => handleTabKeyDown(e, index)}
                className={`relative shrink-0 whitespace-nowrap inline-flex items-center gap-2 px-4 py-3 text-xs transition-colors border-b-[3px] focus-visible:outline-2 focus-visible:outline-[#2A5BDB] ${
                  isSelected
                    ? "border-[#2A5BDB] text-[#102C54] font-bold"
                    : "border-transparent text-textSecondary font-medium hover:text-[#102C54]"
                }`}
              >
                <span>{tab.label}</span>
                {tab.id === "review" && effectiveReviewQueue.length > 0 && (
                  <span className="inline-flex items-center justify-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-900">
                    {effectiveReviewQueue.length}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Active Tab Panel */}
      <div role="tabpanel" aria-labelledby={activeTabMeta.domId} className="space-y-6">
        {resolvedActiveTab === "library" && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            <Card className="xl:col-span-2 border border-appBorder bg-white shadow-sm">
              <CardHeader className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                    <CardTitle className="text-base text-[#102C54]">
                      Corporate Policy Library ({effectivePolicies.length})
                    </CardTitle>
                  </div>
                  <Badge variant={isOffline ? "warning" : "success"}>
                    {isOffline ? "Standby" : "Postgres + pgvector Live"}
                  </Badge>
                </div>
                <CardDescription className="text-xs">
                  Drafts can be inspected and repaired before submitting for Stage 2 publish
                  approval. Active versions power hybrid bilingual search.
                </CardDescription>

                {/* Library Filters */}
                <div role="search" aria-label="Filter corporate policies">
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1 text-xs">
                    <div>
                      <label
                        htmlFor={filterTypeId}
                        className="block text-[11px] font-semibold text-textSecondary mb-1"
                      >
                        Type
                      </label>
                      <select
                        id={filterTypeId}
                        value={docTypeFilter}
                        onChange={(e) => setDocTypeFilter(e.target.value)}
                        className="w-full rounded-small border border-appBorder bg-[#F8F9FD] px-2.5 py-1.5 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
                      >
                        <option value="">All Types</option>
                        <option value="company_policy">Company Policy</option>
                        <option value="labour_law">Labour Law</option>
                        <option value="helpdesk_knowledge">Helpdesk Knowledge</option>
                      </select>
                    </div>

                    <div>
                      <label
                        htmlFor={filterVisibilityId}
                        className="block text-[11px] font-semibold text-textSecondary mb-1"
                      >
                        Visibility
                      </label>
                      <select
                        id={filterVisibilityId}
                        value={visibilityFilter}
                        onChange={(e) => setVisibilityFilter(e.target.value)}
                        className="w-full rounded-small border border-appBorder bg-[#F8F9FD] px-2.5 py-1.5 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
                      >
                        <option value="">All Visibilities</option>
                        <option value="all">All Employees</option>
                        <option value="managers">Managers+</option>
                        <option value="hr_only">HR Only</option>
                      </select>
                    </div>

                    <div>
                      <label
                        htmlFor={filterStatusId}
                        className="block text-[11px] font-semibold text-textSecondary mb-1"
                      >
                        Lifecycle Status
                      </label>
                      <select
                        id={filterStatusId}
                        value={lifecycleFilter}
                        onChange={(e) => setLifecycleFilter(e.target.value)}
                        className="w-full rounded-small border border-appBorder bg-[#F8F9FD] px-2.5 py-1.5 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
                      >
                        <option value="">All Lifecycle States</option>
                        <option value="draft">Draft</option>
                        <option value="review">In Review</option>
                        <option value="active">Active</option>
                        <option value="superseded">Superseded</option>
                        <option value="archived">Archived</option>
                      </select>
                    </div>
                  </div>

                  <div className="mt-2.5 flex flex-wrap items-center gap-4 text-[11px] text-[#102C54]">
                    <label
                      htmlFor={filterReviewId}
                      className="inline-flex items-center gap-1.5 cursor-pointer font-medium"
                    >
                      <input
                        id={filterReviewId}
                        type="checkbox"
                        checked={needsReviewOnly}
                        onChange={(e) => setNeedsReviewOnly(e.target.checked)}
                        className="rounded border-appBorder accent-[#2A5BDB]"
                      />
                      Needs review / failed only
                    </label>
                    <label
                      htmlFor={filterArchivedId}
                      className="inline-flex items-center gap-1.5 cursor-pointer font-medium"
                    >
                      <input
                        id={filterArchivedId}
                        type="checkbox"
                        checked={includeArchived}
                        onChange={(e) => setIncludeArchived(e.target.checked)}
                        className="rounded border-appBorder accent-[#2A5BDB]"
                      />
                      Show archived versions
                    </label>
                  </div>
                </div>
              </CardHeader>

              <CardContent>
                {effectivePoliciesLoading ? (
                  <p className="py-8 text-center text-xs text-textSecondary">
                    Loading corporate policy library...
                  </p>
                ) : effectivePolicies.length === 0 ? (
                  <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center space-y-1">
                    <p className="text-xs font-semibold text-[#102C54]">
                      No corporate policies match the current filters.
                    </p>
                    <p className="text-[11px] text-textSecondary">
                      Upload a PDF, DOCX, or TXT handbook to parse, index in pgvector, and review.
                    </p>
                  </div>
                ) : (
                  <div
                    tabIndex={0}
                    role="region"
                    aria-labelledby={tableCaptionId}
                    className="overflow-auto max-h-[540px] max-w-full rounded-medium border border-appBorder focus-visible:outline-2 focus-visible:outline-[#2A5BDB]"
                  >
                    <table className="w-full min-w-[760px] border-separate border-spacing-0 text-left text-xs">
                      <caption id={tableCaptionId} className="sr-only">
                        Corporate Policy Library
                      </caption>
                      <thead className="sticky top-0 z-10 bg-[#F8F9FD] text-[11px] uppercase tracking-wide text-textSecondary">
                        <tr>
                          <th
                            scope="col"
                            className="border-b border-appBorder px-3.5 py-2.5 font-semibold"
                          >
                            Policy Document
                          </th>
                          <th
                            scope="col"
                            className="border-b border-appBorder px-3 py-2.5 font-semibold"
                          >
                            Version
                          </th>
                          <th
                            scope="col"
                            className="border-b border-appBorder px-3 py-2.5 font-semibold"
                          >
                            Type
                          </th>
                          <th
                            scope="col"
                            className="border-b border-appBorder px-3 py-2.5 font-semibold"
                          >
                            Visibility
                          </th>
                          <th
                            scope="col"
                            className="border-b border-appBorder px-3 py-2.5 font-semibold"
                          >
                            Status
                          </th>
                          <th
                            scope="col"
                            className="border-b border-appBorder px-3 py-2.5 font-semibold"
                          >
                            Effective
                          </th>
                          <th
                            scope="col"
                            className="border-b border-appBorder px-3.5 py-2.5 font-semibold text-right"
                          >
                            Actions
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-appBorder bg-white">
                        {effectivePolicies.map((policy) => {
                          const lifecycle = (policy.lifecycle_status || "draft").toLowerCase();
                          const canSubmitPublish =
                            canManage &&
                            policy.ingestion_status === "indexed" &&
                            lifecycle === "draft";
                          const canReingest =
                            canManage && lifecycle !== "archived" && lifecycle !== "superseded";
                          const canArchive = canManage && lifecycle !== "archived";
                          const lowCount = Array.isArray(policy.low_confidence_pages)
                            ? policy.low_confidence_pages.length
                            : 0;

                          return (
                            <tr key={policy.id} className="hover:bg-[#F8F9FD]/60 align-top">
                              <th
                                scope="row"
                                className="border-b border-appBorder px-3.5 py-3 font-normal text-left min-w-[210px]"
                              >
                                <div className="space-y-0.5">
                                  <Link
                                    href={`/policies/${policy.id}/preview`}
                                    className="font-bold text-[#102C54] hover:text-[#2A5BDB] hover:underline block"
                                  >
                                    {policy.title}
                                  </Link>
                                  <p className="text-[11px] text-textSecondary truncate max-w-[230px]">
                                    {policy.file_name} • {formatBytes(policy.size_bytes)}
                                  </p>
                                  <p className="text-[10px] text-textMuted">
                                    Uploaded {formatPKT(policy.created_at)}
                                  </p>
                                </div>
                              </th>

                              <td className="border-b border-appBorder px-3 py-3 whitespace-nowrap">
                                <Badge variant="outline" className="font-mono text-[11px]">
                                  v{policy.version ?? 1}
                                </Badge>
                              </td>

                              <td className="border-b border-appBorder px-3 py-3 whitespace-nowrap text-[#102C54] font-medium">
                                {formatDocType(policy.doc_type)}
                              </td>

                              <td className="border-b border-appBorder px-3 py-3 whitespace-nowrap">
                                <VisibilityBadge visibility={policy.visibility} />
                              </td>

                              <td className="border-b border-appBorder px-3 py-3">
                                <div className="flex flex-col items-start gap-1.5">
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    <IngestionStatusBadge
                                      status={policy.ingestion_status}
                                      progressPct={policy.progress_pct}
                                      progressStage={policy.progress_stage}
                                    />
                                    <LifecycleStatusBadge status={lifecycle} />
                                  </div>
                                  {lowCount > 0 && (
                                    <span className="inline-flex items-center rounded-small border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-900">
                                      {lowCount} low-confidence page(s)
                                    </span>
                                  )}
                                  {policy.has_prompt_injection_warning && (
                                    <span className="inline-flex items-center gap-1 rounded-small border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-900">
                                      <ShieldAlert className="h-3 w-3" aria-hidden="true" />
                                      Injection warning
                                    </span>
                                  )}
                                  {policy.error_message && (
                                    <span className="inline-flex items-center rounded-small border border-rose-200 bg-rose-50 px-2 py-0.5 text-[10px] font-medium text-rose-800 max-w-[220px]">
                                      {policy.error_message}
                                    </span>
                                  )}
                                </div>
                              </td>

                              <td className="border-b border-appBorder px-3 py-3 whitespace-nowrap text-[11px] text-textSecondary">
                                {policy.effective_date || "—"}
                              </td>

                              <td className="border-b border-appBorder px-3.5 py-3 text-right">
                                <div className="inline-flex flex-wrap items-center justify-end gap-1.5">
                                  <Link
                                    href={`/policies/${policy.id}/preview`}
                                    className="inline-flex items-center gap-1 rounded-small border border-appBorder bg-white px-2.5 py-1 text-[11px] font-semibold text-[#102C54] hover:border-[#2A5BDB] hover:text-[#2A5BDB] transition-colors whitespace-nowrap"
                                  >
                                    Preview &amp; Edit
                                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                                  </Link>

                                  {canSubmitPublish && (
                                    <Button
                                      type="button"
                                      size="sm"
                                      disabled={
                                        pendingPublishId === policy.id ||
                                        publishPolicy.isPending ||
                                        isOffline
                                      }
                                      onClick={() => void handlePublish(policy)}
                                      className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-[11px] px-2.5 py-1 h-auto inline-flex items-center gap-1 whitespace-nowrap"
                                    >
                                      <Send className="h-3 w-3" aria-hidden="true" />
                                      {pendingPublishId === policy.id
                                        ? "Submitting..."
                                        : "Submit for Publish"}
                                    </Button>
                                  )}

                                  {canReingest && (
                                    <Button
                                      type="button"
                                      variant="outline"
                                      size="sm"
                                      disabled={
                                        pendingReingestId === policy.id ||
                                        reingestPolicy.isPending ||
                                        isOffline
                                      }
                                      onClick={() => void handleReingest(policy.id, policy.title)}
                                      aria-label={`Re-ingest policy ${policy.title}`}
                                      className="text-[11px] px-2.5 py-1 h-auto inline-flex items-center gap-1 whitespace-nowrap"
                                    >
                                      <RefreshCw
                                        className={`h-3 w-3 ${
                                          pendingReingestId === policy.id ? "animate-spin" : ""
                                        }`}
                                        aria-hidden="true"
                                      />
                                      Re-ingest
                                    </Button>
                                  )}

                                  {canArchive && (
                                    <Button
                                      type="button"
                                      variant="outline"
                                      size="sm"
                                      disabled={
                                        pendingArchiveId === policy.id ||
                                        deletePolicy.isPending ||
                                        isOffline
                                      }
                                      onClick={() => void handleArchive(policy.id, policy.title)}
                                      aria-label={`Archive policy ${policy.title}`}
                                      className="text-rose-700 border-rose-200 hover:bg-rose-50 text-[11px] px-2.5 py-1 h-auto inline-flex items-center gap-1 whitespace-nowrap"
                                    >
                                      <Archive className="h-3 w-3" aria-hidden="true" />
                                      Archive
                                    </Button>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>

            <PolicyUploadForm canManage={canManage} isOffline={isOffline} />
          </div>
        )}

        {resolvedActiveTab === "search" && (
          <PolicySearchPanel
            userRole={userRole}
            isHrHead={currentUser?.is_hr_head === true}
            isBackendOffline={isOffline}
          />
        )}

        {resolvedActiveTab === "review" && canManage && (
          <PolicyReviewQueue
            items={effectiveReviewQueue}
            isLoading={isReviewQueueLoading}
            error={reviewQueueError ?? null}
            isOffline={isOffline}
            onReingest={handleReingest}
            reingestingId={pendingReingestId}
          />
        )}
      </div>

      {/* Vector Store Architecture Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card className="md:col-span-2 border border-appBorder bg-white">
          <CardHeader>
            <div className="flex items-center justify-between mb-1">
              <div className="h-8 w-8 rounded-medium bg-[#2A5BDB]/10 flex items-center justify-center text-[#2A5BDB]">
                <BookOpenCheck className="h-4 w-4" aria-hidden="true" />
              </div>
              <Badge variant="primary">Agent 4: Policy Brain</Badge>
            </div>
            <CardTitle className="text-sm font-bold text-[#102C54]">
              Bilingual Policy Foundation (ADR 008)
            </CardTitle>
            <CardDescription className="text-xs">
              Deterministic parsing, 768D L2-normalized pgvector embeddings, and Stage 2 publish
              approval governance.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs text-textSecondary">
            <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
              <span className="font-semibold text-[#102C54] flex items-center gap-1.5">
                <Languages className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
                English &amp; Urdu Script Normalization
              </span>
              <p>
                Unicode NFKC + Alef/Yeh canonicalization aligns Urdu queries cleanly with
                multilingual policy embeddings.
              </p>
            </div>
            <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
              <span className="font-semibold text-[#102C54] flex items-center gap-1.5">
                <ShieldCheck className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
                Untrusted Document Defense
              </span>
              <p>
                Prompt-injection markers are flagged at chunking time and enforced alongside tenant
                Row-Level Security.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card className="border border-appBorder bg-white">
          <CardHeader>
            <CardTitle className="text-sm font-bold text-[#102C54]">
              Retrieval Index Status
            </CardTitle>
            <CardDescription className="text-xs">Stage 5 Active Engine</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-2.5">
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Vector Index</span>
              <span className="font-semibold text-[#102C54]">
                pgvector HNSW (768D cosine, ef_search=100)
              </span>
            </div>
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Fusion Strategy</span>
              <span className="font-semibold text-[#102C54]">
                Reciprocal Rank Fusion (Vector + FTS simple + Trigram)
              </span>
            </div>
            <div className="flex items-center gap-1.5 text-emerald-700 font-semibold pt-0.5">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>Cited Q&amp;A arrives in Stage 7</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
