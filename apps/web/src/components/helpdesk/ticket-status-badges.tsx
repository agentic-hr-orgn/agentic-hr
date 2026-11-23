"use client";

import React, { useEffect, useState } from "react";
import type {
  AssigneeRole,
  Ticket,
  TicketCategory,
  TicketPriority,
  TicketStatus,
} from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";

/**
 * Captures client `Date.now()` post-mount so SSR and initial hydration always match
 * and render bodies remain pure under React Compiler `react-hooks/purity`.
 */
export function useClientNowMs(): number | null {
  const [nowMs, setNowMs] = useState<number | null>(null);
  useEffect(() => {
    setNowMs(Date.now());
  }, []);
  return nowMs;
}

export function isTicketSlaBreached(
  ticket: Pick<Ticket, "status" | "sla_due_at" | "sla_escalated_at">,
  nowMs: number | null
): boolean {
  if (ticket.status === "resolved" || ticket.status === "closed") return false;
  if (ticket.sla_escalated_at) return true;
  if (nowMs === null) return false;
  const dueMs = Date.parse(ticket.sla_due_at);
  return Number.isFinite(dueMs) && nowMs > dueMs;
}

export interface StaffAssigneeOption {
  user_id: string;
  full_name: string;
  email: string;
}

export const TICKET_CATEGORIES: readonly TicketCategory[] = [
  "hr_policy",
  "leave_attendance",
  "payroll",
  "it_access",
  "facilities",
  "general",
] as const;

export const TICKET_PRIORITIES: readonly TicketPriority[] = [
  "low",
  "medium",
  "high",
  "urgent",
] as const;

export const TICKET_STATUSES: readonly TicketStatus[] = [
  "open",
  "in_progress",
  "waiting_on_employee",
  "resolved",
  "closed",
] as const;

export const ASSIGNEE_ROLES: readonly AssigneeRole[] = ["hr_head", "owner", "manager"] as const;

export const CATEGORY_LABELS: Record<TicketCategory, string> = {
  hr_policy: "HR Policy",
  leave_attendance: "Leave & Attendance",
  payroll: "Payroll",
  it_access: "IT Access",
  facilities: "Facilities",
  general: "General",
};

export const PRIORITY_LABELS: Record<TicketPriority, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  urgent: "Urgent",
};

export const STATUS_LABELS: Record<TicketStatus, string> = {
  open: "Open",
  in_progress: "In Progress",
  waiting_on_employee: "Waiting on Employee",
  resolved: "Resolved",
  closed: "Closed",
};

export const ASSIGNEE_ROLE_LABELS: Record<AssigneeRole, string> = {
  hr_head: "HR Head",
  owner: "Owner",
  manager: "Manager",
};

export function canAccessStaffQueue(
  user?: { role?: string; is_hr_head?: boolean } | null
): boolean {
  if (!user) return false;
  return user.role === "owner" || user.role === "manager" || Boolean(user.is_hr_head);
}

export function canManageTicketRules(
  user?: { role?: string; is_hr_head?: boolean } | null
): boolean {
  if (!user) return false;
  return user.role === "owner" || Boolean(user.is_hr_head);
}

export function TicketStatusBadge({ status }: { status: TicketStatus }) {
  const variant =
    status === "resolved" || status === "closed"
      ? "success"
      : status === "waiting_on_employee"
        ? "warning"
        : status === "in_progress"
          ? "primary"
          : "secondary";

  return <Badge variant={variant}>{STATUS_LABELS[status] ?? status}</Badge>;
}

export function TicketPriorityBadge({ priority }: { priority: TicketPriority }) {
  if (priority === "urgent") {
    return (
      <Badge variant="outline" className="border-rose-300 bg-rose-50 text-rose-800">
        {PRIORITY_LABELS[priority]}
      </Badge>
    );
  }
  const variant = priority === "high" ? "warning" : priority === "medium" ? "primary" : "outline";
  return <Badge variant={variant}>{PRIORITY_LABELS[priority] ?? priority}</Badge>;
}

export function TicketCategoryBadge({ category }: { category: TicketCategory }) {
  return (
    <Badge variant="outline" className="bg-white text-[#102C54]">
      {CATEGORY_LABELS[category] ?? category}
    </Badge>
  );
}

export function TicketSlaBadge({
  ticket,
}: {
  ticket: Pick<
    Ticket,
    "status" | "sla_due_at" | "sla_reminder_at" | "sla_reminder_sent_at" | "sla_escalated_at"
  >;
}) {
  const nowMs = useClientNowMs();

  if (ticket.status === "resolved" || ticket.status === "closed") {
    return <Badge variant="success">Met SLA</Badge>;
  }

  if (isTicketSlaBreached(ticket, nowMs)) {
    return (
      <Badge variant="outline" className="border-rose-300 bg-rose-50 text-rose-800 font-semibold">
        SLA Breached
      </Badge>
    );
  }

  const remMs = Date.parse(ticket.sla_reminder_at);
  const reminderByTime = nowMs !== null && Number.isFinite(remMs) && nowMs >= remMs;

  if (ticket.sla_reminder_sent_at || reminderByTime) {
    return <Badge variant="warning">Reminder Sent</Badge>;
  }

  return <Badge variant="success">On Track</Badge>;
}
