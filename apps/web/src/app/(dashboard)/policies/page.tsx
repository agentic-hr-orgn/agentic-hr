"use client";

import React, { useId, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  BookOpenCheck,
  FileText,
  Languages,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useDeletePolicy, usePolicies, useUploadPolicy } from "@/hooks/use-policies";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";
import { useToastStore } from "@/stores/toast-store";

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".txt"];
const MAX_POLICY_BYTES = 10 * 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export default function PoliciesPage() {
  const titleInputId = useId();
  const fileInputId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const { data: currentUser } = useCurrentUser();
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
  } = usePolicies();
  const uploadPolicy = useUploadPolicy();
  const deletePolicy = useDeletePolicy();
  const pushToast = useToastStore((s) => s.pushToast);

  const [policyTitle, setPolicyTitle] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const userRole = currentUser?.role?.toLowerCase() || "owner";
  const canManage =
    userRole === "owner" || userRole === "manager" || currentUser?.is_hr_head === true;

  const isOffline = isBackendStandby || isOfflineError(policiesError);
  const liveHttpError =
    policiesError && !isOfflineError(policiesError) ? policiesError.message : null;
  const displayedError = formError || liveHttpError;

  const handleUploadSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canManage) return;
    setFormError(null);

    if (!selectedFile) {
      setFormError("Please select a policy document (PDF, DOCX, or TXT).");
      return;
    }

    const lowerName = selectedFile.name.toLowerCase();
    if (!ALLOWED_EXTENSIONS.some((ext) => lowerName.endsWith(ext))) {
      setFormError(
        `Unsupported file format '${selectedFile.name}'. Allowed formats: PDF, DOCX, TXT.`
      );
      return;
    }
    if (selectedFile.size <= 0) {
      setFormError("Uploaded policy file cannot be empty.");
      return;
    }
    if (selectedFile.size > MAX_POLICY_BYTES) {
      setFormError("Policy file exceeds the 10MB upload limit.");
      return;
    }

    try {
      const uploaded = await uploadPolicy.mutateAsync({
        file: selectedFile,
        title: policyTitle.trim() || undefined,
      });
      setPolicyTitle("");
      setSelectedFile(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      pushToast({
        variant: "success",
        title: "Policy uploaded",
        description: `'${uploaded.title}' queued for Stage 5 ingestion.`,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to upload policy document.";
      setFormError(message);
      pushToast({ variant: "error", title: "Policy upload failed", description: message });
    }
  };

  const handleDelete = async (policyId: string, title: string) => {
    if (!canManage) return;
    if (typeof window !== "undefined" && !window.confirm(`Delete policy '${title}'?`)) {
      return;
    }
    setFormError(null);
    try {
      await deletePolicy.mutateAsync(policyId);
      pushToast({
        variant: "success",
        title: "Policy deleted",
        description: `Removed '${title}' from tenant policy vault.`,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to delete policy document.";
      setFormError(message);
      pushToast({ variant: "error", title: "Delete failed", description: message });
    }
  };

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
            Upload corporate HR handbooks in PostgreSQL today; Stage 5 activates pgvector indexing
            and bilingual English &amp; Urdu RAG search.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="primary">Stage 4 Live Vault</Badge>
          <Badge variant="outline">Stage 5 RAG Next</Badge>
        </div>
      </div>

      {isOffline && (
        <StandbyBanner
          message="FastAPI backend is offline. Corporate Policy uploads and deletions require a live backend connection."
          onRetry={() => {
            refetchHealth();
            refetchPolicies();
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
          {formError && (
            <button
              type="button"
              onClick={() => setFormError(null)}
              className="font-bold text-rose-700 hover:text-rose-900 px-1"
              aria-label="Dismiss policy error"
            >
              &times;
            </button>
          )}
        </div>
      )}

      {/* Live Corporate Policy Documents Vault */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2 border border-appBorder bg-white shadow-sm">
          <CardHeader>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                <CardTitle className="text-base text-[#102C54]">
                  Uploaded Corporate Policies ({policies.length})
                </CardTitle>
              </div>
              <Badge variant={isOffline ? "warning" : "success"}>
                {isOffline ? "Standby" : "Postgres Live"}
              </Badge>
            </div>
            <CardDescription className="text-xs">
              Tenant-isolated policy documents stored securely and queued for Stage 5 pgvector
              chunking.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isPoliciesLoading ? (
              <p className="py-6 text-center text-xs text-textSecondary">
                Loading corporate policy documents...
              </p>
            ) : policies.length === 0 ? (
              <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center space-y-1">
                <p className="text-xs font-semibold text-[#102C54]">
                  No corporate policies uploaded yet.
                </p>
                <p className="text-[11px] text-textSecondary">
                  Upload PDF, DOCX, or TXT employee handbooks using the upload form to prepare your
                  Stage 5 Policy Brain knowledge base.
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-appBorder">
                {policies.map((policy) => (
                  <li
                    key={policy.id}
                    className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                  >
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-bold text-[#102C54] truncate">
                          {policy.title}
                        </span>
                        <Badge variant="outline" className="text-[10px] font-mono">
                          {policy.ingestion_status}
                        </Badge>
                      </div>
                      <p className="text-[11px] text-textSecondary truncate">
                        {policy.file_name} • {formatBytes(policy.size_bytes)} • Uploaded{" "}
                        {formatPKT(policy.created_at)}
                      </p>
                    </div>
                    {canManage && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void handleDelete(policy.id, policy.title)}
                        disabled={deletePolicy.isPending || isOffline}
                        aria-label={`Delete policy ${policy.title}`}
                        className="text-rose-700 border-rose-200 hover:bg-rose-50 text-xs inline-flex items-center gap-1 shrink-0"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        Delete
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Upload Policy Form */}
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardHeader>
            <div className="flex items-center gap-2">
              <UploadCloud className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              <CardTitle className="text-sm font-bold text-[#102C54]">
                Upload Policy Document
              </CardTitle>
            </div>
            <CardDescription className="text-xs">
              Supported: PDF, DOCX, UTF-8 TXT (max 10MB). Verified via magic-byte inspection.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {canManage ? (
              <form onSubmit={(e) => void handleUploadSubmit(e)} className="space-y-3 text-xs">
                <div>
                  <label htmlFor={titleInputId} className="block font-semibold text-[#102C54] mb-1">
                    Policy Title (Optional)
                  </label>
                  <input
                    id={titleInputId}
                    type="text"
                    value={policyTitle}
                    onChange={(e) => setPolicyTitle(e.target.value)}
                    placeholder="e.g., 2026 Leave & Attendance Handbook"
                    className="w-full rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
                  />
                </div>
                <div>
                  <label htmlFor={fileInputId} className="block font-semibold text-[#102C54] mb-1">
                    Document File
                  </label>
                  <input
                    id={fileInputId}
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.docx,.txt"
                    onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
                    className="w-full text-xs text-textSecondary file:mr-3 file:rounded-small file:border-0 file:bg-[#2A5BDB]/10 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-[#2A5BDB] hover:file:bg-[#2A5BDB]/20"
                  />
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
                Uploading or deleting corporate policy documents requires Owner, Manager, or HR Head
                privileges.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Agent Overview & Vector Config */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card className="md:col-span-2 border border-appBorder bg-white">
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-[#2A5BDB]/10 flex items-center justify-center text-[#2A5BDB]">
                <BookOpenCheck className="h-5 w-5" aria-hidden="true" />
              </div>
              <Badge variant="primary">Agent 4: Policy Brain</Badge>
            </div>
            <CardTitle className="text-base text-[#102C54]">
              Bilingual Policy Query Assistant (Stage 5)
            </CardTitle>
            <CardDescription className="text-xs">
              Retrieval-Augmented Generation using PostgreSQL 16 pgvector embeddings.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-4">
            <p>
              Employees can inquire in English or Urdu regarding leave balances, maternity/paternity
              policies, remote work regulations, and reimbursement rules. Answers strictly cite
              approved handbook paragraphs.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
                <span className="font-semibold text-[#102C54] flex items-center gap-1.5">
                  <Languages className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
                  Urdu Native Support
                </span>
                <span>
                  Multilingual embeddings (Gemini Embedding 2) tuned for English and Urdu policy
                  corpuses.
                </span>
              </div>
              <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
                <span className="font-semibold text-[#102C54] flex items-center gap-1.5">
                  <ShieldCheck className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
                  Source-Citations Only
                </span>
                <span>
                  Untrusted document isolation with strict clause citations and RLS filtering.
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="border border-appBorder bg-white">
          <CardHeader>
            <CardTitle className="text-sm font-bold text-[#102C54]">
              Vector Store (ADR 002)
            </CardTitle>
            <CardDescription className="text-xs">pgvector Configuration</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Database Extension</span>
              <span className="font-semibold text-[#102C54]">pgvector (Active in Postgres 16)</span>
            </div>
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Embedding Dimensions</span>
              <span className="font-semibold text-[#102C54]">768 / 1536 (MRL Optimized)</span>
            </div>
            <div>
              <span className="text-textMuted block">Multi-Tenant Isolation</span>
              <span className="font-semibold text-[#102C54]">
                Row-Level Security on vector queries
              </span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Stage 5 Interactive Policy Preview */}
      <Card className="border-appBorder bg-surface">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              <CardTitle className="text-sm font-semibold text-[#102C54]">
                Interactive Policy Assistant (Stage 5 Bilingual RAG Preview)
              </CardTitle>
            </div>
            <Badge variant="outline">Stage 5 Preview</Badge>
          </div>
          <CardDescription className="text-xs">
            Sample English &amp; Urdu query flow powered by tenant-uploaded corporate handbooks.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="p-3 rounded-medium bg-appBg border border-appBorder text-xs space-y-1">
              <div className="flex items-center justify-between text-textMuted text-[10px]">
                <span>Employee Query (Urdu):</span>
                <span>Urdu Language Detected</span>
              </div>
              <p className="font-semibold text-textPrimary text-right font-sans" dir="rtl">
                کیا مجھے سالانہ چھٹیوں کے دوران بیماری کی رخصت مل سکتی ہے؟
              </p>
            </div>

            <div className="p-3 rounded-medium bg-[#2A5BDB]/5 border border-[#2A5BDB]/20 text-xs space-y-1">
              <div className="flex items-center justify-between text-[#2A5BDB] text-[10px] font-semibold">
                <span>Policy Brain Response:</span>
                <span>Cited: Leave Policy Sec 4.2</span>
              </div>
              <p className="text-textPrimary text-right font-sans" dir="rtl">
                جی ہاں، اگر سالانہ رخصت کے دوران آپ بیمار ہو جائیں تو مستند ڈاکٹر کی تصدیق شدہ
                میڈیکل رپورٹ جمع کرا کر ان دنوں کو بیماری کی رخصت (Sick Leave) میں تبدیل کیا جا سکتا
                ہے۔
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
