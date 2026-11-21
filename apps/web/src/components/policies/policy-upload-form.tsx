"use client";

import React, { useId, useRef, useState } from "react";
import { UploadCloud, XCircle } from "lucide-react";
import type { PolicyDocumentItem } from "@agentic-hr/contracts";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useUploadPolicy } from "@/hooks/use-policies";
import { isOfflineError } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".txt"];
const MAX_POLICY_BYTES = 10 * 1024 * 1024;

interface PolicyUploadFormProps {
  canManage: boolean;
  isOffline?: boolean;
  onUploaded?: (doc: PolicyDocumentItem) => void;
}

export function PolicyUploadForm({
  canManage,
  isOffline = false,
  onUploaded,
}: PolicyUploadFormProps) {
  const headingId = useId();
  const titleId = useId();
  const docTypeId = useId();
  const visibilityId = useId();
  const languageId = useId();
  const effectiveDateId = useId();
  const sourceId = useId();
  const fileId = useId();
  const errorId = useId();

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const uploadPolicy = useUploadPolicy();
  const pushToast = useToastStore((s) => s.pushToast);

  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("company_policy");
  const [visibility, setVisibility] = useState("all");
  const [language, setLanguage] = useState("auto");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [source, setSource] = useState("upload");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const validateFile = (file: File | null): string | null => {
    if (!file) {
      return "Please select a policy document (PDF, DOCX, or TXT).";
    }
    const lowerName = file.name.toLowerCase();
    if (!ALLOWED_EXTENSIONS.some((ext) => lowerName.endsWith(ext))) {
      return `Unsupported file format '${file.name}'. Allowed formats: PDF, DOCX, TXT.`;
    }
    if (file.size <= 0) {
      return "Uploaded policy file cannot be empty.";
    }
    if (file.size > MAX_POLICY_BYTES) {
      return "Policy file exceeds the 10MB upload limit.";
    }
    return null;
  };

  const handleFileChange = (file: File | null) => {
    setSelectedFile(file);
    if (file) {
      const err = validateFile(file);
      setFormError(err);
    } else {
      setFormError(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!canManage) return;

    const validationError = validateFile(selectedFile);
    if (validationError || !selectedFile) {
      setFormError(validationError || "Please select a policy document.");
      fileInputRef.current?.focus();
      return;
    }

    setFormError(null);
    try {
      const uploaded = await uploadPolicy.mutateAsync({
        file: selectedFile,
        title: title.trim() || undefined,
        docType,
        visibility,
        language: language === "auto" ? undefined : language,
        effectiveDate: effectiveDate.trim() || undefined,
        source: source.trim() || "upload",
      });

      setTitle("");
      setEffectiveDate("");
      setLanguage("auto");
      setSource("upload");
      setSelectedFile(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }

      pushToast({
        variant: "success",
        title: "Policy uploaded",
        description: `'${uploaded.title}' (v${uploaded.version ?? 1}) — ${uploaded.ingestion_status}.`,
      });
      onUploaded?.(uploaded);
    } catch (err: unknown) {
      const message = isOfflineError(err)
        ? "FastAPI backend is offline; policy upload cannot proceed."
        : err instanceof Error
          ? err.message
          : "Failed to upload policy document.";
      setFormError(message);
      pushToast({ variant: "error", title: "Policy upload failed", description: message });
    }
  };

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader>
        <div className="flex items-center gap-2">
          <UploadCloud className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
          <CardTitle id={headingId} className="text-sm font-bold text-[#102C54]">
            Upload Policy Document
          </CardTitle>
        </div>
        <CardDescription className="text-xs">
          PDF, DOCX, or UTF-8 TXT (max 10MB). New uploads with an existing title create draft v2+.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {canManage ? (
          <form
            aria-labelledby={headingId}
            onSubmit={(e) => void handleSubmit(e)}
            noValidate
            className="space-y-3 text-xs"
          >
            {formError && (
              <div
                id={errorId}
                role="alert"
                className="flex items-start justify-between gap-2 rounded-small border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900"
              >
                <div className="flex items-start gap-1.5">
                  <XCircle
                    className="h-3.5 w-3.5 text-rose-600 shrink-0 mt-0.5"
                    aria-hidden="true"
                  />
                  <span className="font-medium">{formError}</span>
                </div>
                <button
                  type="button"
                  onClick={() => setFormError(null)}
                  aria-label="Dismiss upload validation error"
                  className="font-bold text-rose-700 hover:text-rose-900"
                >
                  &times;
                </button>
              </div>
            )}

            <div>
              <label htmlFor={titleId} className="block font-semibold text-[#102C54] mb-1">
                Policy Title (Optional)
              </label>
              <input
                id={titleId}
                name="title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g., 2026 Leave & Attendance Handbook"
                className="w-full rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor={docTypeId} className="block font-semibold text-[#102C54] mb-1">
                  Document Type
                </label>
                <select
                  id={docTypeId}
                  name="doc_type"
                  value={docType}
                  onChange={(e) => setDocType(e.target.value)}
                  className="w-full rounded-small border border-appBorder bg-white px-2.5 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                >
                  <option value="company_policy">Company Policy</option>
                  <option value="labour_law">Labour Law</option>
                  <option value="helpdesk_knowledge">Helpdesk Knowledge</option>
                </select>
              </div>

              <div>
                <label htmlFor={visibilityId} className="block font-semibold text-[#102C54] mb-1">
                  Visibility
                </label>
                <select
                  id={visibilityId}
                  name="visibility"
                  value={visibility}
                  onChange={(e) => setVisibility(e.target.value)}
                  className="w-full rounded-small border border-appBorder bg-white px-2.5 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                >
                  <option value="all">All Employees</option>
                  <option value="managers">Managers+</option>
                  <option value="hr_only">HR Only</option>
                  <option value="owners">Owners Only</option>
                </select>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor={languageId} className="block font-semibold text-[#102C54] mb-1">
                  Language
                </label>
                <select
                  id={languageId}
                  name="language"
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  className="w-full rounded-small border border-appBorder bg-white px-2.5 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                >
                  <option value="auto">Auto-detect (EN / UR)</option>
                  <option value="en">English (en)</option>
                  <option value="ur">Urdu (ur)</option>
                  <option value="mixed">Bilingual (mixed)</option>
                </select>
              </div>

              <div>
                <label
                  htmlFor={effectiveDateId}
                  className="block font-semibold text-[#102C54] mb-1"
                >
                  Effective Date (Optional)
                </label>
                <input
                  id={effectiveDateId}
                  name="effective_date"
                  type="date"
                  value={effectiveDate}
                  onChange={(e) => setEffectiveDate(e.target.value)}
                  className="w-full rounded-small border border-appBorder bg-white px-2.5 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                />
              </div>
            </div>

            <div>
              <label htmlFor={sourceId} className="block font-semibold text-[#102C54] mb-1">
                Source
              </label>
              <input
                id={sourceId}
                name="source"
                type="text"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder="upload"
                className="w-full rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              />
            </div>

            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragActive(true);
              }}
              onDragLeave={() => setIsDragActive(false)}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragActive(false);
                handleFileChange(e.dataTransfer.files?.[0] ?? null);
              }}
              className={`rounded-medium border-2 border-dashed p-3.5 transition-colors ${
                isDragActive
                  ? "border-[#2A5BDB] bg-[#2A5BDB]/5"
                  : "border-slate-300 bg-[#F8F9FD] hover:border-[#2A5BDB]/60"
              }`}
            >
              <label htmlFor={fileId} className="block font-semibold text-[#102C54] mb-1.5">
                Document File <span aria-hidden="true">*</span>
              </label>
              <input
                id={fileId}
                ref={fileInputRef}
                name="file"
                type="file"
                accept=".pdf,.docx,.txt"
                required
                aria-invalid={formError ? "true" : undefined}
                aria-describedby={formError ? errorId : undefined}
                onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
                className="w-full text-xs text-textSecondary file:mr-3 file:rounded-small file:border-0 file:bg-[#2A5BDB]/10 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-[#2A5BDB] hover:file:bg-[#2A5BDB]/20"
              />
              {selectedFile && (
                <p className="mt-1.5 text-[11px] font-medium text-[#102C54] truncate">
                  Selected: {selectedFile.name}
                </p>
              )}
            </div>

            <Button
              type="submit"
              disabled={uploadPolicy.isPending || isOffline}
              className="w-full bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs inline-flex items-center justify-center gap-1.5"
            >
              <UploadCloud className="h-3.5 w-3.5" aria-hidden="true" />
              {uploadPolicy.isPending ? "Uploading Policy..." : "Upload Policy"}
            </Button>
          </form>
        ) : (
          <p className="text-xs text-textSecondary">
            Uploading, editing, or publishing corporate policy documents requires Owner or HR Head
            privileges.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
