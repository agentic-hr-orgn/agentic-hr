"use client";

import React from "react";
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  Clock,
  FileEdit,
  History,
  Loader2,
  Lock,
  Shield,
  Users,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { AuthenticatedUserContext } from "@/hooks/use-current-user";

/**
 * Matches backend Stage 5 `require_hr_head` RBAC guard (Owner or HR Head).
 * Kept outside `hooks/use-policies.ts` so `vi.mock("@/hooks/use-policies")`
 * in tests never mocks out synchronous RBAC checks.
 */
export function canManagePolicies(user: AuthenticatedUserContext | null | undefined): boolean {
  if (!user) return false;
  return user.role?.toLowerCase() === "owner" || user.is_hr_head === true;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function formatDocType(docType: string | null | undefined): string {
  switch (docType) {
    case "company_policy":
      return "Company Policy";
    case "labour_law":
      return "Labour Law";
    case "helpdesk_knowledge":
      return "Helpdesk Knowledge";
    default:
      return docType || "Company Policy";
  }
}

export function formatVisibility(visibility: string | null | undefined): string {
  switch (visibility) {
    case "all":
      return "All Employees";
    case "managers":
      return "Managers+";
    case "hr_only":
      return "HR Only";
    default:
      return visibility || "All Employees";
  }
}

export function formatLanguageLabel(language: string | null | undefined): string {
  switch (language?.toLowerCase()) {
    case "en":
      return "EN";
    case "ur":
      return "UR";
    case "mixed":
      return "EN + UR";
    default:
      return "Auto";
  }
}

interface IngestionStatusBadgeProps {
  status: string;
  progressPct?: number;
  progressStage?: string | null;
}

export function IngestionStatusBadge({
  status,
  progressPct = 0,
  progressStage,
}: IngestionStatusBadgeProps) {
  const normalized = (status || "").toLowerCase();

  if (normalized === "indexed") {
    return (
      <Badge variant="success" className="gap-1 text-[11px] whitespace-nowrap">
        <CheckCircle2 className="h-3 w-3 shrink-0" aria-hidden="true" />
        Indexed
      </Badge>
    );
  }

  if (normalized === "needs_review") {
    return (
      <Badge variant="warning" className="gap-1 text-[11px] whitespace-nowrap">
        <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden="true" />
        Needs Review
      </Badge>
    );
  }

  if (normalized === "failed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-rose-200 bg-rose-50 px-2.5 py-0.5 text-[11px] font-semibold text-rose-700 whitespace-nowrap">
        <XCircle className="h-3 w-3 shrink-0" aria-hidden="true" />
        Failed
      </span>
    );
  }

  if (normalized === "queued" || normalized === "processing") {
    const pct = Math.max(0, Math.min(100, Number.isFinite(progressPct) ? progressPct : 0));
    const isProcessing = normalized === "processing";
    return (
      <div className="inline-flex flex-col gap-1">
        <Badge variant="primary" className="gap-1 text-[11px] whitespace-nowrap">
          {isProcessing ? (
            <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden="true" />
          ) : (
            <Clock className="h-3 w-3 shrink-0" aria-hidden="true" />
          )}
          {isProcessing ? `Processing (${pct}%)` : `Queued (${pct}%)`}
        </Badge>
        <div className="flex items-center gap-1.5 min-w-[110px]">
          <progress
            value={pct}
            max={100}
            aria-label={`Ingestion progress ${pct}%`}
            className="h-1.5 w-20 overflow-hidden rounded-full accent-[#2A5BDB]"
          />
          {progressStage && (
            <span className="text-[10px] text-textSecondary truncate max-w-[90px]">
              {progressStage}
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <Badge variant="outline" className="gap-1 text-[11px] font-mono whitespace-nowrap">
      <Clock className="h-3 w-3 shrink-0" aria-hidden="true" />
      {status || "unknown"}
    </Badge>
  );
}

export function LifecycleStatusBadge({ status }: { status: string | null | undefined }) {
  const normalized = (status || "draft").toLowerCase();

  if (normalized === "active") {
    return (
      <Badge variant="success" className="gap-1 text-[11px] whitespace-nowrap">
        <Shield className="h-3 w-3 shrink-0" aria-hidden="true" />
        Active
      </Badge>
    );
  }

  if (normalized === "review") {
    return (
      <Badge variant="primary" className="gap-1 text-[11px] whitespace-nowrap">
        <Clock className="h-3 w-3 shrink-0" aria-hidden="true" />
        In Review
      </Badge>
    );
  }

  if (normalized === "superseded") {
    return (
      <Badge variant="outline" className="gap-1 text-[11px] whitespace-nowrap">
        <History className="h-3 w-3 shrink-0" aria-hidden="true" />
        Superseded
      </Badge>
    );
  }

  if (normalized === "archived") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-100 px-2.5 py-0.5 text-[11px] font-semibold text-slate-600 whitespace-nowrap">
        <Archive className="h-3 w-3 shrink-0" aria-hidden="true" />
        Archived
      </span>
    );
  }

  return (
    <Badge variant="secondary" className="gap-1 text-[11px] whitespace-nowrap">
      <FileEdit className="h-3 w-3 shrink-0" aria-hidden="true" />
      Draft
    </Badge>
  );
}

export function VisibilityBadge({ visibility }: { visibility: string | null | undefined }) {
  const normalized = (visibility || "all").toLowerCase();
  const isRestricted = normalized === "hr_only";
  const Icon = isRestricted ? Lock : Users;

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ${
        normalized === "hr_only"
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : normalized === "managers"
            ? "border-[#2A5BDB]/20 bg-[#2A5BDB]/5 text-[#2A5BDB]"
            : "border-appBorder bg-[#F8F9FD] text-textSecondary"
      }`}
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
      {formatVisibility(normalized)}
    </span>
  );
}
