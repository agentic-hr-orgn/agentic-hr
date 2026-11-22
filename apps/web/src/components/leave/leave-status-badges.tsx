"use client";

import React from "react";
import { Badge } from "@/components/ui/badge";
import type { AuthenticatedUserContext } from "@/hooks/use-current-user";

export interface ParsedRuleReason {
  rule: string;
  status: string;
  code: string;
  message: string;
}

export function canApproveLeave(user: AuthenticatedUserContext | null | undefined): boolean {
  if (!user) return false;
  const role = (user.role || "").toLowerCase();
  return role === "owner" || role === "manager" || user.is_hr_head === true;
}

export function canManageLeave(user: AuthenticatedUserContext | null | undefined): boolean {
  if (!user) return false;
  const role = (user.role || "").toLowerCase();
  return role === "owner" || user.is_hr_head === true;
}

export function toNumericDays(val: number | string | null | undefined): number {
  if (val === null || val === undefined || val === "") return 0;
  const parsed = typeof val === "number" ? val : Number(val);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function formatDays(val: number | string | null | undefined): string {
  const n = toNumericDays(val);
  const rounded = Math.round(n * 100) / 100;
  if (Number.isInteger(rounded)) return String(rounded);
  return rounded.toFixed(2).replace(/0$/, "");
}

export function extractRuleReasons(raw: unknown): ParsedRuleReason[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item) => ({
      rule: String(item.rule ?? "policy"),
      status: String(item.status ?? "OK").toUpperCase(),
      code: String(item.code ?? ""),
      message: String(item.message ?? ""),
    }));
}

export function LeaveStatusBadge({ status }: { status: string | null | undefined }) {
  const norm = (status || "pending").toLowerCase();
  if (norm === "approved") {
    return (
      <Badge variant="success" className="text-[11px]">
        Approved
      </Badge>
    );
  }
  if (norm === "pending_hr_exception") {
    return (
      <Badge variant="warning" className="text-[11px]">
        Pending HR Exception
      </Badge>
    );
  }
  if (norm === "rejected") {
    return (
      <Badge variant="outline" className="text-[11px] bg-rose-50 text-rose-700 border-rose-200">
        Rejected
      </Badge>
    );
  }
  if (norm === "cancelled") {
    return (
      <Badge variant="outline" className="text-[11px] bg-slate-50 text-slate-600 border-slate-200">
        Cancelled
      </Badge>
    );
  }
  if (norm === "expired") {
    return (
      <Badge variant="outline" className="text-[11px] bg-amber-50 text-amber-800 border-amber-200">
        Expired
      </Badge>
    );
  }
  return (
    <Badge variant="primary" className="text-[11px]">
      Pending
    </Badge>
  );
}

export function PolicyCheckBadge({ status }: { status: string | null | undefined }) {
  const norm = (status || "OK").toUpperCase();
  if (norm === "VIOLATION") {
    return (
      <Badge variant="warning" className="text-[10px]">
        Policy Violation
      </Badge>
    );
  }
  if (norm === "BLOCKED") {
    return (
      <Badge variant="outline" className="text-[10px] bg-rose-50 text-rose-700 border-rose-200">
        Blocked
      </Badge>
    );
  }
  return (
    <Badge variant="success" className="text-[10px]">
      Policy OK
    </Badge>
  );
}
