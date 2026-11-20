"use client";

import React, { useId, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileText,
  Trash2,
  UploadCloud,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useDeletePolicy, usePolicies, useUploadPolicy } from "@/hooks/use-policies";
import { useCurrentUser } from "@/hooks/use-current-user";
import { isOfflineError } from "@/lib/api-request";

interface Step2UploadPoliciesProps {
  isSavingStep: boolean;
  onBack: () => void;
  onSaveAndContinue: () => void;
}

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".txt"];
const MAX_FILE_BYTES = 10 * 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function Step2UploadPolicies({
  isSavingStep,
  onBack,
  onSaveAndContinue,
}: Step2UploadPoliciesProps) {
  const titleInputId = useId();
  const fileInputId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const { data: currentUser } = useCurrentUser();
  const { data: policies = [], isLoading, error: listError } = usePolicies();
  const uploadMutation = useUploadPolicy();
  const deleteMutation = useDeletePolicy();

  const [policyTitle, setPolicyTitle] = useState("");
  const [isDragActive, setIsDragActive] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const userRole = currentUser?.role?.toLowerCase() || "owner";
  const canManage =
    userRole === "owner" || userRole === "manager" || currentUser?.is_hr_head === true;

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
      });
      setPolicyTitle("");
      setSuccessMessage(`Uploaded '${uploaded.title}' (${formatBytes(uploaded.size_bytes)}).`);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to upload policy document.");
    }
  };

  const handleDelete = async (policyId: string, title: string) => {
    if (!canManage) return;
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      await deleteMutation.mutateAsync(policyId);
      setSuccessMessage(`Removed policy '${title}'.`);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to delete policy document.");
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
            10MB). Documents are stored securely and queued for Stage 5 Policy Brain AI indexing.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Optional Custom Title */}
          <div className="max-w-md space-y-1.5">
            <label htmlFor={titleInputId} className="block text-xs font-semibold text-textPrimary">
              Document Title (Optional)
            </label>
            <input
              id={titleInputId}
              name="policy_title"
              type="text"
              value={policyTitle}
              onChange={(e) => setPolicyTitle(e.target.value)}
              placeholder="e.g. Employee Leave & Attendance Handbook 2026"
              disabled={!canManage || uploadMutation.isPending}
              className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary placeholder:text-slate-400 focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
            />
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
                      <div className="min-w-0">
                        <p className="text-xs font-bold text-[#102C54] truncate">{doc.title}</p>
                        <p className="text-[11px] text-textSecondary truncate">
                          {doc.file_name} • {formatBytes(doc.size_bytes)} • Storage:{" "}
                          <span className="font-mono">{doc.storage_provider}</span>
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <Badge variant="primary" className="text-[11px]">
                        Queued for Stage 5 AI Ingestion
                      </Badge>
                      {canManage && (
                        <button
                          type="button"
                          onClick={() => void handleDelete(doc.id, doc.title)}
                          disabled={deleteMutation.isPending}
                          aria-label={`Delete policy ${doc.title}`}
                          className="inline-flex items-center gap-1 rounded-small border border-rose-200 bg-rose-50 px-2.5 py-1 text-[11px] font-semibold text-rose-700 hover:bg-rose-100 disabled:opacity-50"
                        >
                          <Trash2 className="h-3 w-3" aria-hidden="true" />
                          Delete
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
