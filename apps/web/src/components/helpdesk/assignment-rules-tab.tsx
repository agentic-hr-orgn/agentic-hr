"use client";

import React, { useState } from "react";
import { Save, Settings2, XCircle } from "lucide-react";
import type { AssigneeRole, TicketAssignmentRule } from "@agentic-hr/contracts";
import {
  ASSIGNEE_ROLES,
  ASSIGNEE_ROLE_LABELS,
  CATEGORY_LABELS,
  type StaffAssigneeOption,
} from "@/components/helpdesk/ticket-status-badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useUpdateTicketRule } from "@/hooks/use-tickets";
import { isOfflineError } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

interface AssignmentRuleRowProps {
  rule: TicketAssignmentRule;
  staffOptions: StaffAssigneeOption[];
  isOffline: boolean;
}

function AssignmentRuleRow({ rule, staffOptions, isOffline }: AssignmentRuleRowProps) {
  const updateRule = useUpdateTicketRule();
  const pushToast = useToastStore((s) => s.pushToast);

  const [role, setRole] = useState<AssigneeRole>(rule.default_assignee_role);
  const [assigneeUserId, setAssigneeUserId] = useState<string>(rule.default_assignee_user_id ?? "");
  const [slaHours, setSlaHours] = useState<number>(rule.sla_hours);
  const [reminderBeforeHours, setReminderBeforeHours] = useState<number>(
    rule.reminder_before_hours
  );
  const [reopenDays, setReopenDays] = useState<number>(rule.reopen_window_days);
  const [rowError, setRowError] = useState<string | null>(null);

  const categoryLabel = CATEGORY_LABELS[rule.category] ?? rule.category;

  const handleSave = async () => {
    if (updateRule.isPending || isOffline) return;
    setRowError(null);

    if (slaHours < 1 || slaHours > 720) {
      setRowError(`${categoryLabel}: SLA hours must be between 1 and 720.`);
      return;
    }
    if (reminderBeforeHours < 0 || reminderBeforeHours >= slaHours) {
      setRowError(`${categoryLabel}: Reminder hours must be strictly less than SLA hours.`);
      return;
    }
    if (reopenDays < 0 || reopenDays > 90) {
      setRowError(`${categoryLabel}: Reopen window must be between 0 and 90 days.`);
      return;
    }

    try {
      await updateRule.mutateAsync({
        category: rule.category,
        payload: {
          default_assignee_role: role,
          default_assignee_user_id: assigneeUserId ? assigneeUserId : null,
          sla_hours: slaHours,
          reminder_before_hours: reminderBeforeHours,
          reopen_window_days: reopenDays,
        },
      });
      pushToast({
        variant: "success",
        title: `Saved ${categoryLabel} Rule`,
        description: `SLA ${slaHours}h, Reminder -${reminderBeforeHours}h, Reopen ${reopenDays}d.`,
      });
    } catch (err: unknown) {
      if (!isOfflineError(err)) {
        setRowError(err instanceof Error ? err.message : "Failed to save assignment rule.");
      }
    }
  };

  return (
    <tr className="border-b border-appBorder/70 last:border-b-0">
      <td className="py-3 pr-3 align-top">
        <p className="text-xs font-bold text-[#102C54]">{categoryLabel}</p>
        <p className="font-mono text-[10px] text-textSecondary">{rule.category}</p>
        {rowError && (
          <p role="alert" className="mt-1 flex items-center gap-1 text-[11px] text-rose-700">
            <XCircle className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span>{rowError}</span>
          </p>
        )}
      </td>

      <td className="py-3 pr-2 align-top">
        <select
          aria-label={`${categoryLabel} default assignee role`}
          value={role}
          onChange={(e) => setRole(e.target.value as AssigneeRole)}
          disabled={isOffline}
          className="w-full rounded-small border border-appBorder bg-white px-2 py-1.5 text-xs text-[#102C54]"
        >
          {ASSIGNEE_ROLES.map((r) => (
            <option key={r} value={r}>
              {ASSIGNEE_ROLE_LABELS[r]}
            </option>
          ))}
        </select>
      </td>

      <td className="py-3 pr-2 align-top">
        <select
          aria-label={`${categoryLabel} default assignee user`}
          value={assigneeUserId}
          onChange={(e) => setAssigneeUserId(e.target.value)}
          disabled={isOffline}
          className="w-full rounded-small border border-appBorder bg-white px-2 py-1.5 text-xs text-[#102C54]"
        >
          <option value="">Fallback to Role</option>
          {staffOptions.map((emp) => (
            <option key={emp.user_id} value={emp.user_id}>
              {emp.full_name}
            </option>
          ))}
        </select>
      </td>

      <td className="py-3 pr-2 align-top">
        <input
          type="number"
          min={1}
          max={720}
          aria-label={`${categoryLabel} SLA hours`}
          value={slaHours}
          onChange={(e) => setSlaHours(Number(e.target.value) || 0)}
          disabled={isOffline}
          className="w-20 rounded-small border border-appBorder bg-white px-2 py-1.5 text-xs text-[#102C54]"
        />
      </td>

      <td className="py-3 pr-2 align-top">
        <input
          type="number"
          min={0}
          max={719}
          aria-label={`${categoryLabel} reminder before hours`}
          value={reminderBeforeHours}
          onChange={(e) => setReminderBeforeHours(Number(e.target.value) || 0)}
          disabled={isOffline}
          className="w-20 rounded-small border border-appBorder bg-white px-2 py-1.5 text-xs text-[#102C54]"
        />
      </td>

      <td className="py-3 pr-2 align-top">
        <input
          type="number"
          min={0}
          max={90}
          aria-label={`${categoryLabel} reopen window days`}
          value={reopenDays}
          onChange={(e) => setReopenDays(Number(e.target.value) || 0)}
          disabled={isOffline}
          className="w-20 rounded-small border border-appBorder bg-white px-2 py-1.5 text-xs text-[#102C54]"
        />
      </td>

      <td className="py-3 align-top text-right">
        <Button
          type="button"
          size="sm"
          disabled={updateRule.isPending || isOffline}
          onClick={() => void handleSave()}
          aria-label={`Save ${categoryLabel} rule`}
          className="h-7 px-2.5 text-xs bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1"
        >
          <Save className="h-3 w-3" aria-hidden="true" />
          {updateRule.isPending ? "Saving..." : "Save"}
        </Button>
      </td>
    </tr>
  );
}

interface AssignmentRulesTabProps {
  rules: TicketAssignmentRule[];
  isLoading: boolean;
  staffOptions: StaffAssigneeOption[];
  isOffline: boolean;
}

export function AssignmentRulesTab({
  rules,
  isLoading,
  staffOptions,
  isOffline,
}: AssignmentRulesTabProps) {
  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Settings2 className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
          <div>
            <CardTitle className="text-base text-[#102C54]">
              Category Routing &amp; SLA Rules
            </CardTitle>
            <CardDescription className="text-xs">
              Configure default assignee roles, specific owners, SLA deadlines, reminder lead time,
              and post-resolution reopen windows per Helpdesk category.
            </CardDescription>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        {isLoading ? (
          <p className="py-8 text-center text-xs text-textSecondary">
            Loading category routing rules...
          </p>
        ) : rules.length === 0 ? (
          <p className="py-8 text-center text-xs text-textSecondary">
            No assignment rules configured yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-appBorder text-[11px] font-semibold uppercase tracking-wide text-textSecondary">
                  <th className="py-2 pr-3">Category</th>
                  <th className="py-2 pr-2">Default Role</th>
                  <th className="py-2 pr-2">Default Assignee</th>
                  <th className="py-2 pr-2">SLA (hrs)</th>
                  <th className="py-2 pr-2">Reminder (hrs)</th>
                  <th className="py-2 pr-2">Reopen (days)</th>
                  <th className="py-2 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <AssignmentRuleRow
                    key={`${rule.category}-${rule.updated_at}`}
                    rule={rule}
                    staffOptions={staffOptions}
                    isOffline={isOffline}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
