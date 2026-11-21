"use client";

import React, { useId, useRef, useState } from "react";
import Link from "next/link";
import {
  Archive,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  FileText,
  UploadCloud,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  IngestionStatusBadge,
  LifecycleStatusBadge,
  VisibilityBadge,
  canManagePolicies,
  formatBytes,
  formatDocType,
} from "@/components/policies/policy-status-badges";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useDeletePolicy, usePolicies, useUploadPolicy } from "@/hooks/use-policies";
import { isOfflineError } from "@/lib/api-request";

interface Step2UploadPoliciesProps {
  isSavingStep: boolean;
  onBack: () => void;
  onSaveAndContinue: () => void;
}

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".txt"];
const MAX_FILE_BYTES = 10 * 1024 * 1024;

export function Step2UploadPolicies({
  isSavingStep,
  onBack,
  onSaveAndContinue,
}: Step2UploadPoliciesProps) {
  const titleInputId = useId();
  const docTypeInputId = useId();
  const visibilityInputId = useId();
  const languageInputId = useId();
  const fileInputId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const { data: currentUser } = useCurrentUser();
  const { data: policies = [], isLoading, error: listError } = usePolicies();
  const uploadMutation = useUploadPolicy();
  const deleteMutation = useDeletePolicy();

  const [policyTitle, setPolicyTitle] = useState("");
  const [docType, setDocType] = useState("company_policy");
  const [visibility, setVisibility] = useState("all");
  const [language, setLanguage] = useState("auto");
  const [isDragActive, setIsDragActive] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const canManage = canManagePolicies(currentUser);
  const liveListError = listError && !isOfflineError(listError) ? listError.message : null;

  const handleFileSelected = async (file: File | undefined | null) => {
    if (!file || !canManage) return;
    setErrorMessage(null);
    setSuccessMessage(null);

    const lowerName = file.name.toLowerCase();
    const validExt = ALLOWED_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
    if (!validExt) {
      setErrorMessage(
        `Unsupported file format '${file.name}'. Please upload PDF, DOCX, or TXT files.`
      );
      return;
    }
    if (file.size <= 0) {
      setErrorMessage("Uploaded policy file cannot be empty.");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setErrorMessage("Policy file exceeds maximum 10MB size limit.");
      return;
    }

    try {
      const uploaded = await uploadMutation.mutateAsync({
        file,
        title: policyTitle.trim() || undefined,
        docType,
        visibility,
        language: language === "auto" ? undefined : language,
      });
      setPolicyTitle("");
      setSuccessMessage(
        `Uploaded '${uploaded.title}' (${formatBytes(uploaded.size_bytes)}) — status: ${uploaded.ingestion_status}.`
      );
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to upload policy document.");
    }
  };

  const handleArchive = async (policyId: string, title: string) => {
    if (!canManage) return;
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      await deleteMutation.mutateAsync(policyId);
      setSuccessMessage(`Archived policy '${title}'.`);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to archive policy document.");
    }
  };

  return (
    <div className="space-y-6">
      {(errorMessage || liveListError) && (
        <div
          role="alert"
          className="flex items-center justify-between rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{errorMessage || liveListError}</span>
          </div>
          {errorMessage && (
            <button
              type="button"
              onClick={() => setErrorMessage(null)}
              className="font-bold text-rose-700 hover:text-rose-900 px-1"
              aria-label="Dismiss error"
            >
              &times;
            </button>
          )}
        </div>
      )}

      {successMessage && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 rounded-medium border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-medium text-emerald-800"
        >
          <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
          <span>{successMessage}</span>
        </div>
      )}

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="text-base text-[#102C54]">
            Step 2 — Upload Corporate HR Policies
          </CardTitle>
          <CardDescription className="text-xs text-textSecondary">
            Upload your Employee Handbook, Leave Policy, or Code of Conduct (PDF, DOCX, TXT up to
            10MB). Documents are parsed, normalized, and indexed in PostgreSQL pgvector immediately.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Metadata Controls */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div className="md:col-span-1 space-y-1">
              <label
                htmlFor={titleInputId}
                className="block text-xs font-semibold text-textPrimary"
              >
                Document Title (Optional)
              </label>
              <input
                id={titleInputId}
                name="policy_title"
                type="text"
                value={policyTitle}
                onChange={(e) => setPolicyTitle(e.target.value)}
                placeholder="e.g. Leave Handbook 2026"
                disabled={!canManage || uploadMutation.isPending}
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary placeholder:text-slate-400 focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              />
            </div>

            <div className="space-y-1">
              <label
                htmlFor={docTypeInputId}
                className="block text-xs font-semibold text-textPrimary"
              >
                Document Type
              </label>
              <select
                id={docTypeInputId}
                name="doc_type"
                value={docType}
                onChange={(e) => setDocType(e.target.value)}
                disabled={!canManage || uploadMutation.isPending}
                className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              >
                <option value="company_policy">Company Policy</option>
                <option value="labour_law">Labour Law</option>
                <option value="helpdesk_knowledge">Helpdesk Knowledge</option>
              </select>
            </div>

            <div className="space-y-1">
              <label
                htmlFor={visibilityInputId}
                className="block text-xs font-semibold text-textPrimary"
              >
                Visibility
              </label>
              <select
                id={visibilityInputId}
                name="visibility"
                value={visibility}
                onChange={(e) => setVisibility(e.target.value)}
                disabled={!canManage || uploadMutation.isPending}
                className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              >
                <option value="all">All Employees</option>
                <option value="managers">Managers+</option>
                <option value="hr_only">HR Only</option>
                <option value="owners">Owners Only</option>
              </select>
            </div>

            <div className="space-y-1">
              <label
                htmlFor={languageInputId}
                className="block text-xs font-semibold text-textPrimary"
              >
                Language
              </label>
              <select
                id={languageInputId}
                name="language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                disabled={!canManage || uploadMutation.isPending}
                className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              >
                <option value="auto">Auto-detect (EN / UR)</option>
                <option value="en">English (en)</option>
                <option value="ur">Urdu (ur)</option>
                <option value="mixed">Bilingual (mixed)</option>
              </select>
            </div>
          </div>

          {/* Drag-and-Drop / File Picker */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragActive(true);
            }}
            onDragLeave={() => setIsDragActive(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragActive(false);
              void handleFileSelected(e.dataTransfer.files?.[0]);
            }}
            className={`rounded-medium border-2 border-dashed p-6 text-center transition-colors ${
              isDragActive
                ? "border-[#2A5BDB] bg-[#2A5BDB]/5"
                : "border-slate-300 bg-[#F8F9FD] hover:border-[#2A5BDB]/60"
            }`}
          >
            <UploadCloud className="mx-auto h-8 w-8 text-[#2A5BDB] mb-2" aria-hidden="true" />
            <p className="text-sm font-semibold text-[#102C54]">
              Drag &amp; drop a policy document here, or browse from disk
            </p>
            <p className="mt-1 text-xs text-textSecondary">
              Supported: PDF (.pdf), Word (.docx), Plain Text (.txt) — Max 10MB
            </p>
            <div className="mt-4">
              <label
                htmlFor={fileInputId}
                className={`inline-flex items-center gap-2 rounded-small px-4 py-2 text-xs font-semibold text-white transition-colors ${
                  canManage && !uploadMutation.isPending
                    ? "cursor-pointer bg-[#2A5BDB] hover:bg-[#102C54]"
                    : "cursor-not-allowed bg-slate-400"
                }`}
              >
                <UploadCloud className="h-3.5 w-3.5" aria-hidden="true" />
                {uploadMutation.isPending ? "Uploading Policy..." : "Select Policy File"}
              </label>
              <input
                ref={fileInputRef}
                id={fileInputId}
                type="file"
                accept=".pdf,.docx,.txt"
                disabled={!canManage || uploadMutation.isPending}
                onChange={(e) => void handleFileSelected(e.target.files?.[0])}
                className="sr-only"
              />
            </div>
          </div>

          {/* Uploaded Policies List */}
          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-bold uppercase tracking-wider text-[#102C54]">
                Uploaded Policies ({policies.length})
              </h4>
            </div>

            {isLoading ? (
              <div className="space-y-2 animate-pulse" aria-busy="true">
                <div className="h-14 rounded-small bg-slate-100" />
                <div className="h-14 rounded-small bg-slate-100" />
              </div>
            ) : policies.length === 0 ? (
              <div className="rounded-small border border-dashed border-appBorder bg-[#F8F9FD] p-5 text-center text-xs text-textSecondary">
                No policy documents uploaded yet. Upload at least 1 policy document to satisfy Step
                5 Go-Live readiness.
              </div>
            ) : (
              <ul className="space-y-2">
                {policies.map((doc) => (
                  <li
                    key={doc.id}
                    className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-small border border-appBorder bg-white p-3.5"
                  >
                    <div className="flex items-start gap-3 min-w-0">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-small bg-[#102C54]/5 text-[#102C54]">
                        <FileText className="h-4 w-4" aria-hidden="true" />
                      </div>
                      <div className="min-w-0 space-y-0.5">
                        <p className="text-xs font-bold text-[#102C54] truncate">
                          {doc.title}{" "}
                          <span className="font-mono text-[10px]">v{doc.version ?? 1}</span>
                        </p>
                        <p className="text-[11px] text-textSecondary truncate">
                          {doc.file_name} • {formatBytes(doc.size_bytes)} •{" "}
                          {formatDocType(doc.doc_type)}
                        </p>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 shrink-0">
                      <VisibilityBadge visibility={doc.visibility} />
                      <IngestionStatusBadge
                        status={doc.ingestion_status}
                        progressPct={doc.progress_pct}
                        progressStage={doc.progress_stage}
                      />
                      <LifecycleStatusBadge status={doc.lifecycle_status} />
                      <Link
                        href={`/policies/${doc.id}/preview`}
                        className="inline-flex items-center gap-1 rounded-small border border-appBorder bg-[#F8F9FD] px-2.5 py-1 text-[11px] font-semibold text-[#102C54] hover:border-[#2A5BDB] hover:text-[#2A5BDB]"
                      >
                        Preview &amp; Fix
                        <ExternalLink className="h-3 w-3" aria-hidden="true" />
                      </Link>
                      {canManage && (
                        <button
                          type="button"
                          onClick={() => void handleArchive(doc.id, doc.title)}
                          disabled={deleteMutation.isPending}
                          aria-label={`Archive policy ${doc.title}`}
                          className="inline-flex items-center gap-1 rounded-small border border-rose-200 bg-rose-50 px-2.5 py-1 text-[11px] font-semibold text-rose-700 hover:bg-rose-100 disabled:opacity-50"
                        >
                          <Archive className="h-3 w-3" aria-hidden="true" />
                          Archive
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between pt-2">
        <Button type="button" variant="outline" onClick={onBack}>
          <ArrowLeft className="mr-1.5 h-4 w-4" aria-hidden="true" />
          Back
        </Button>
        <Button
          type="button"
          onClick={onSaveAndContinue}
          disabled={isSavingStep || !canManage}
          className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-2"
        >
          {isSavingStep ? "Saving Step 2..." : "Save & Continue to Company Structure"}
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
