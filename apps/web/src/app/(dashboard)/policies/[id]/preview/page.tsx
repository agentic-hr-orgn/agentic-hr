"use client";

import React, { useId, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Edit3,
  FileText,
  Info,
  RefreshCw,
  Save,
  Send,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import type { PolicyChunkPreviewItem, PolicyPagePreviewItem } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import {
  IngestionStatusBadge,
  LifecycleStatusBadge,
  VisibilityBadge,
  canManagePolicies,
  formatBytes,
  formatDocType,
  formatLanguageLabel,
} from "@/components/policies/policy-status-badges";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import {
  useFixPolicyChunk,
  useFixPolicyPage,
  usePolicyPreview,
  usePublishPolicy,
  useReingestPolicy,
} from "@/hooks/use-policies";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";
import { useToastStore } from "@/stores/toast-store";

export default function PolicyPreviewPage() {
  const params = useParams();
  const policyId = typeof params?.id === "string" ? params.id : "";

  const sectionInputId = useId();
  const chunkTextInputId = useId();
  const markReviewedId = useId();
  const pageTextInputId = useId();

  const { data: currentUser } = useCurrentUser();
  const canManage = canManagePolicies(currentUser);

  const {
    isStandby: isBackendStandby,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();

  const {
    data: preview,
    isLoading,
    error: previewError,
    refetch: refetchPreview,
    isFetching: isPreviewFetching,
  } = usePolicyPreview(policyId);

  const fixPage = useFixPolicyPage(policyId);
  const fixChunk = useFixPolicyChunk(policyId);
  const publishPolicy = usePublishPolicy();
  const reingestPolicy = useReingestPolicy();
  const pushToast = useToastStore((s) => s.pushToast);

  const [editingPageNumber, setEditingPageNumber] = useState<number | null>(null);
  const [pageDraftText, setPageDraftText] = useState("");
  const [editingChunkId, setEditingChunkId] = useState<string | null>(null);
  const [chunkDraftText, setChunkDraftText] = useState("");
  const [chunkDraftSection, setChunkDraftSection] = useState("");
  const [chunkMarkReviewed, setChunkMarkReviewed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const isOffline = isBackendStandby || isOfflineError(previewError);
  const liveHttpError = previewError && !isOfflineError(previewError) ? previewError.message : null;
  const displayedError = actionError || liveHttpError;

  const openPageEditor = (page: PolicyPagePreviewItem) => {
    setActionError(null);
    setEditingPageNumber(page.page_number);
    setPageDraftText(page.cleaned_text || "");
  };

  const openChunkEditor = (chunk: PolicyChunkPreviewItem) => {
    setActionError(null);
    setEditingChunkId(chunk.id);
    setChunkDraftText(chunk.text);
    setChunkDraftSection(chunk.section_path);
    setChunkMarkReviewed(false);
  };

  const handleSavePageFix = async (e: React.FormEvent, pageNumber: number) => {
    e.preventDefault();
    if (!canManage || fixPage.isPending || isOffline) return;
    if (!pageDraftText.trim()) {
      setActionError(`Page ${pageNumber} repaired text cannot be blank.`);
      return;
    }

    setActionError(null);
    try {
      const updated = await fixPage.mutateAsync({
        pageNumber,
        payload: { cleaned_text: pageDraftText },
      });
      setEditingPageNumber(null);
      pushToast({
        variant: "success",
        title: `Page ${pageNumber} updated`,
        description: `Document re-indexed — status is now '${updated.ingestion_status}'.`,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to save repaired page text.";
      setActionError(message);
      pushToast({ variant: "error", title: "Page repair failed", description: message });
    }
  };

  const handleSaveChunkEdit = async (e: React.FormEvent, chunkId: string) => {
    e.preventDefault();
    if (!canManage || fixChunk.isPending || isOffline) return;
    if (!chunkDraftText.trim()) {
      setActionError("Chunk text cannot be empty.");
      return;
    }

    setActionError(null);
    try {
      await fixChunk.mutateAsync({
        chunkId,
        payload: {
          text: chunkDraftText,
          section_path: chunkDraftSection.trim() || null,
          mark_reviewed: chunkMarkReviewed,
        },
      });
      setEditingChunkId(null);
      pushToast({
        variant: "success",
        title: "Section chunk updated",
        description: "Updated text normalized and re-embedded cleanly in pgvector.",
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to save chunk edit.";
      setActionError(message);
      pushToast({ variant: "error", title: "Chunk edit failed", description: message });
    }
  };

  const handlePublish = async () => {
    if (!preview || !canManage || publishPolicy.isPending || isOffline) return;
    setActionError(null);
    try {
      await publishPolicy.mutateAsync({
        policyId: preview.document.id,
        payload: { approver_role: "owner" },
      });
      pushToast({
        variant: "success",
        title: "Submitted for Publish Approval",
        description: `'${preview.document.title}' (v${preview.document.version ?? 1}) is pending review in Approvals Inbox.`,
        actionLabel: "Open Approvals Inbox",
        actionHref: "/approvals",
      });
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to submit policy for publish approval.";
      setActionError(message);
      pushToast({ variant: "error", title: "Publish request failed", description: message });
    }
  };

  const handleReingest = async () => {
    if (!preview || !canManage || reingestPolicy.isPending || isOffline) return;
    setActionError(null);
    try {
      const updated = await reingestPolicy.mutateAsync(preview.document.id);
      pushToast({
        variant: "success",
        title: "Policy re-ingestion triggered",
        description: `Ingestion status is now '${updated.ingestion_status}'.`,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to trigger re-ingestion.";
      setActionError(message);
      pushToast({ variant: "error", title: "Re-ingest failed", description: message });
    }
  };

  const doc = preview?.document;
  const pages = preview?.pages ?? [];
  const chunks = preview?.chunks ?? [];
  const lifecycle = (doc?.lifecycle_status ?? "draft").toLowerCase();
  const isDraft = lifecycle === "draft";
  const canPublish = Boolean(
    doc && canManage && doc.ingestion_status === "indexed" && isDraft && !isOffline
  );

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      {/* Top Breadcrumb */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-appBorder pb-4">
        <div className="flex items-center gap-2 text-xs">
          <Link
            href="/policies"
            className="text-textSecondary hover:text-[#2A5BDB] inline-flex items-center gap-1 font-semibold transition-colors"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            Back to Policy Library
          </Link>
          <span className="text-textMuted">/</span>
          <span className="font-semibold text-[#102C54]">Preview &amp; Fix</span>
        </div>
        <Badge variant="outline">Stage 5 Policy Inspection</Badge>
      </div>

      {isOffline && (
        <StandbyBanner
          message="FastAPI backend is offline. Previewing and editing policy content requires an active backend connection."
          onRetry={() => {
            refetchHealth();
            refetchPreview();
          }}
          isRetrying={isHealthFetching || isPreviewFetching}
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
              aria-label="Dismiss error"
              className="font-bold text-rose-700 hover:text-rose-900 px-1"
            >
              &times;
            </button>
          )}
        </div>
      )}

      {isLoading ? (
        <Card className="border border-appBorder bg-white">
          <CardContent className="py-12 text-center text-xs text-textSecondary">
            Loading extracted policy pages and chunks...
          </CardContent>
        </Card>
      ) : !doc ? (
        <Card className="border border-appBorder bg-white">
          <CardContent className="py-10 text-center space-y-3">
            <p className="text-xs font-semibold text-[#102C54]">
              Policy document preview is unavailable.
            </p>
            <Link href="/policies">
              <Button variant="outline" size="sm">
                Return to Policy Library
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Document Header & Publish Gate */}
          <Card className="border border-appBorder bg-white shadow-sm">
            <CardHeader>
              <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <FileText className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
                    <h1 className="text-xl font-bold text-[#102C54]">{doc.title}</h1>
                    <Badge variant="outline" className="font-mono text-xs">
                      v{doc.version ?? 1}
                    </Badge>
                    <Badge variant="secondary">{formatDocType(doc.doc_type)}</Badge>
                    <VisibilityBadge visibility={doc.visibility} />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <IngestionStatusBadge
                      status={doc.ingestion_status}
                      progressPct={doc.progress_pct}
                      progressStage={doc.progress_stage}
                    />
                    <LifecycleStatusBadge status={lifecycle} />
                    {doc.has_prompt_injection_warning && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2.5 py-0.5 text-[11px] font-semibold text-amber-900">
                        <ShieldAlert className="h-3 w-3" aria-hidden="true" />
                        Prompt-Injection Warning
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-textSecondary">
                    {doc.file_name} • {formatBytes(doc.size_bytes)} • Pages: {doc.page_count} •
                    Source: <span className="font-mono">{doc.source}</span> • Effective Date:{" "}
                    {doc.effective_date || "Immediate"} • Updated {formatPKT(doc.updated_at)}
                  </p>
                </div>

                {canManage && (
                  <div className="flex flex-col items-start lg:items-end gap-2 shrink-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {lifecycle !== "archived" && lifecycle !== "superseded" && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={reingestPolicy.isPending || isOffline}
                          onClick={() => void handleReingest()}
                          className="text-xs inline-flex items-center gap-1.5"
                        >
                          <RefreshCw
                            className={`h-3.5 w-3.5 ${
                              reingestPolicy.isPending ? "animate-spin" : ""
                            }`}
                            aria-hidden="true"
                          />
                          {reingestPolicy.isPending ? "Re-ingesting..." : "Re-ingest"}
                        </Button>
                      )}

                      <Button
                        type="button"
                        disabled={!canPublish || publishPolicy.isPending}
                        onClick={() => void handlePublish()}
                        className="bg-[#2A5BDB] text-white hover:bg-[#102C54] disabled:bg-slate-300 text-xs inline-flex items-center gap-1.5"
                      >
                        <Send className="h-3.5 w-3.5" aria-hidden="true" />
                        {publishPolicy.isPending
                          ? "Submitting Approval..."
                          : "Submit for Publish Approval"}
                      </Button>
                    </div>

                    {!canPublish && (
                      <p className="text-[11px] text-textSecondary max-w-sm lg:text-right">
                        {lifecycle === "review" ? (
                          <>
                            Pending Stage 2 approval.{" "}
                            <Link
                              href="/approvals"
                              className="text-[#2A5BDB] font-semibold underline"
                            >
                              Open Approvals Inbox
                            </Link>
                          </>
                        ) : lifecycle === "active" ? (
                          "This version is Active and live in bilingual policy search."
                        ) : lifecycle === "superseded" || lifecycle === "archived" ? (
                          `This ${lifecycle} version cannot be submitted for publishing.`
                        ) : (
                          "Fix any low-confidence OCR pages and ensure indexing finishes before publishing."
                        )}
                      </p>
                    )}
                  </div>
                )}
              </div>
            </CardHeader>

            {!isDraft && (
              <CardContent className="pt-0">
                <div className="flex items-center gap-2 rounded-small border border-appBorder bg-[#F8F9FD] px-3.5 py-2.5 text-xs text-[#102C54]">
                  <Info className="h-4 w-4 text-[#2A5BDB] shrink-0" aria-hidden="true" />
                  <span>
                    Published, in-review, superseded, and archived versions are immutable. Upload a
                    new file with the same title to create an editable draft v
                    {(doc.version ?? 1) + 1}.
                  </span>
                </div>
              </CardContent>
            )}
          </Card>

          {/* Pages & OCR Review Panel */}
          <Card className="border border-appBorder bg-white shadow-sm">
            <CardHeader>
              <CardTitle className="text-base text-[#102C54]">
                Extracted Pages &amp; OCR Review ({pages.length})
              </CardTitle>
              <CardDescription className="text-xs">
                Inspect per-page extraction quality scores. Pages flagged{" "}
                <code>Needs OCR / Manual Fix</code> can be repaired inline to clear review holds.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {pages.length === 0 ? (
                <p className="text-xs text-textSecondary">
                  No extracted pages recorded yet for this document.
                </p>
              ) : (
                pages.map((page) => {
                  const isLowQuality = page.needs_ocr || page.quality_score < 0.7;
                  const isEditingThisPage = editingPageNumber === page.page_number;

                  return (
                    <div
                      key={page.id}
                      className={`rounded-medium border p-4 space-y-3 ${
                        isLowQuality
                          ? "border-amber-300 bg-amber-50/40"
                          : "border-appBorder bg-[#F8F9FD]"
                      }`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-bold text-[#102C54]">
                            Page {page.page_number}
                          </span>
                          <Badge variant="outline" className="text-[10px] font-mono">
                            {formatLanguageLabel(page.language)}
                          </Badge>
                          <Badge
                            variant={isLowQuality ? "warning" : "success"}
                            className="text-[10px]"
                          >
                            Quality: {Math.round(page.quality_score * 100)}%
                          </Badge>
                          {isLowQuality ? (
                            <span className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-100 px-2.5 py-0.5 text-[10px] font-bold text-amber-950">
                              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                              Needs OCR / Manual Fix
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700">
                              <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                              {page.ocr_applied ? "OCR Applied" : "Clean Extraction"}
                            </span>
                          )}
                        </div>

                        {canManage && isDraft && !isEditingThisPage && (
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => openPageEditor(page)}
                            aria-label={`Fix Page ${page.page_number} Text`}
                            className="text-xs h-auto py-1 px-2.5 inline-flex items-center gap-1"
                          >
                            <Edit3 className="h-3 w-3" aria-hidden="true" />
                            Fix Page Text
                          </Button>
                        )}
                      </div>

                      {page.error_message && (
                        <p className="text-[11px] font-medium text-rose-800 bg-rose-50 border border-rose-200 rounded-small px-2.5 py-1.5">
                          {page.error_message}
                        </p>
                      )}

                      {isEditingThisPage ? (
                        <form
                          onSubmit={(e) => void handleSavePageFix(e, page.page_number)}
                          className="space-y-3 bg-white p-3 rounded-small border border-appBorder"
                        >
                          <div>
                            <label
                              htmlFor={pageTextInputId}
                              className="block text-xs font-semibold text-[#102C54] mb-1"
                            >
                              Repaired Text for Page {page.page_number}
                            </label>
                            <textarea
                              id={pageTextInputId}
                              name="cleaned_text"
                              dir="auto"
                              rows={6}
                              value={pageDraftText}
                              onChange={(e) => setPageDraftText(e.target.value)}
                              className="w-full rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
                            />
                          </div>
                          <div className="flex flex-wrap items-center gap-2">
                            <Button
                              type="submit"
                              size="sm"
                              disabled={fixPage.isPending || isOffline}
                              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs inline-flex items-center gap-1"
                            >
                              <Save className="h-3.5 w-3.5" aria-hidden="true" />
                              {fixPage.isPending
                                ? "Saving & Re-indexing..."
                                : "Save Fix & Re-index"}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => setEditingPageNumber(null)}
                              className="text-xs"
                            >
                              Cancel
                            </Button>
                          </div>
                        </form>
                      ) : (
                        <p
                          dir="auto"
                          className="rounded-small bg-white border border-appBorder px-3 py-2.5 text-xs leading-relaxed text-[#102C54] whitespace-pre-wrap"
                        >
                          {page.cleaned_text ||
                            "(Empty page text — click Fix Page Text to supply text)"}
                        </p>
                      )}
                    </div>
                  );
                })
              )}
            </CardContent>
          </Card>

          {/* Parsed Sections & Chunks Panel */}
          <Card className="border border-appBorder bg-white shadow-sm">
            <CardHeader>
              <CardTitle className="text-base text-[#102C54]">
                Parsed Sections &amp; Indexed Chunks ({chunks.length})
              </CardTitle>
              <CardDescription className="text-xs">
                Chunks preserve hierarchical section paths and page provenance. Editing a draft
                chunk updates only its section and embedding vector.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {chunks.length === 0 ? (
                <p className="text-xs text-textSecondary">
                  No indexed chunks available yet for this document.
                </p>
              ) : (
                chunks.map((chunk) => {
                  const isEditingThisChunk = editingChunkId === chunk.id;
                  return (
                    <div
                      key={chunk.id}
                      className="rounded-medium border border-appBorder bg-[#F8F9FD] p-4 space-y-2.5"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="space-y-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs font-bold text-[#102C54]">
                              Chunk #{chunk.chunk_index + 1}
                            </span>
                            <Badge variant="outline" className="text-[10px] font-mono">
                              {chunk.page_range}
                            </Badge>
                            <Badge variant="secondary" className="text-[10px]">
                              {chunk.token_count} tokens
                            </Badge>
                            <Badge variant="outline" className="text-[10px] font-mono">
                              {formatLanguageLabel(chunk.language)}
                            </Badge>
                            <VisibilityBadge visibility={chunk.visibility} />
                          </div>
                          <p className="text-[11px] font-semibold text-[#2A5BDB]">
                            {chunk.section_path}
                          </p>
                        </div>

                        {canManage && isDraft && !isEditingThisChunk && (
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => openChunkEditor(chunk)}
                            aria-label={`Edit Chunk ${chunk.chunk_index + 1}`}
                            className="text-xs h-auto py-1 px-2.5 inline-flex items-center gap-1"
                          >
                            <Edit3 className="h-3 w-3" aria-hidden="true" />
                            Edit Chunk
                          </Button>
                        )}
                      </div>

                      {chunk.has_injection_flag && (
                        <div className="inline-flex items-center gap-1.5 rounded-small border border-amber-300 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-900">
                          <ShieldAlert className="h-3.5 w-3.5 text-amber-700" aria-hidden="true" />
                          Prompt-Injection Flagged (Untrusted Text)
                        </div>
                      )}

                      {isEditingThisChunk ? (
                        <form
                          onSubmit={(e) => void handleSaveChunkEdit(e, chunk.id)}
                          className="space-y-3 bg-white p-3 rounded-small border border-appBorder"
                        >
                          <div>
                            <label
                              htmlFor={sectionInputId}
                              className="block text-xs font-semibold text-[#102C54] mb-1"
                            >
                              Section Path
                            </label>
                            <input
                              id={sectionInputId}
                              name="section_path"
                              type="text"
                              value={chunkDraftSection}
                              onChange={(e) => setChunkDraftSection(e.target.value)}
                              className="w-full rounded-small border border-appBorder bg-white px-3 py-1.5 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
                            />
                          </div>

                          <div>
                            <label
                              htmlFor={chunkTextInputId}
                              className="block text-xs font-semibold text-[#102C54] mb-1"
                            >
                              Chunk Text
                            </label>
                            <textarea
                              id={chunkTextInputId}
                              name="text"
                              dir="auto"
                              rows={5}
                              value={chunkDraftText}
                              onChange={(e) => setChunkDraftText(e.target.value)}
                              className="w-full rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
                            />
                          </div>

                          <label
                            htmlFor={markReviewedId}
                            className="inline-flex items-center gap-2 text-xs text-[#102C54] cursor-pointer"
                          >
                            <input
                              id={markReviewedId}
                              type="checkbox"
                              checked={chunkMarkReviewed}
                              onChange={(e) => setChunkMarkReviewed(e.target.checked)}
                              className="rounded border-appBorder accent-[#2A5BDB]"
                            />
                            Clear prompt-injection warning and mark chunk reviewed
                          </label>

                          <div className="flex flex-wrap items-center gap-2">
                            <Button
                              type="submit"
                              size="sm"
                              disabled={fixChunk.isPending || isOffline}
                              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs inline-flex items-center gap-1"
                            >
                              <Save className="h-3.5 w-3.5" aria-hidden="true" />
                              {fixChunk.isPending ? "Saving Chunk..." : "Save Chunk"}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => setEditingChunkId(null)}
                              className="text-xs"
                            >
                              Cancel
                            </Button>
                          </div>
                        </form>
                      ) : (
                        <p
                          dir="auto"
                          className="rounded-small bg-white border border-appBorder px-3 py-2.5 text-xs leading-relaxed text-[#102C54] whitespace-pre-wrap"
                        >
                          {chunk.text}
                        </p>
                      )}
                    </div>
                  );
                })
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
