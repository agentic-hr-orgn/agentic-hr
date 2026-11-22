"use client";

import React, { useState } from "react";
import { AlertTriangle, CheckCircle2, Inbox, XCircle } from "lucide-react";
import type { LeaveRequestResponse } from "@agentic-hr/contracts";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  LeaveStatusBadge,
  PolicyCheckBadge,
  extractRuleReasons,
  formatDays,
} from "@/components/leave/leave-status-badges";
import { useDecideLeaveRequest } from "@/hooks/use-leave";
import { useToastStore } from "@/stores/toast-store";

interface ManagerInboxTabProps {
  requests: LeaveRequestResponse[];
  isLoading: boolean;
  isOffline: boolean;
}

export function ManagerInboxTab({ requests, isLoading, isOffline }: ManagerInboxTabProps) {
  const [notesById, setNotesById] = useState<Record<string, string>>({});
  const [pendingId, setPendingId] = useState<string | null>(null);
  const decideMutation = useDecideLeaveRequest();
  const pushToast = useToastStore((s) => s.pushToast);

  const handleDecision = async (req: LeaveRequestResponse, decision: "approved" | "rejected") => {
    if (decideMutation.isPending || isOffline) return;
    const noteVal = (notesById[req.id] || "").trim();
    setPendingId(req.id);
    try {
      const updated = await decideMutation.mutateAsync({
        requestId: req.id,
        payload: {
          decision,
          ...(noteVal ? { note: noteVal } : {}),
        },
      });
      pushToast({
        variant: "success",
        title: decision === "approved" ? "Leave Request Approved" : "Leave Request Rejected",
        description: `${updated.employee_name}'s ${updated.leave_type_name} (${updated.start_date}) is now '${updated.status}'.`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to record decision.";
      pushToast({ variant: "error", title: "Decision Failed", description: msg });
    } finally {
      setPendingId(null);
    }
  };

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Inbox className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
          <CardTitle className="text-base text-[#102C54]">
            Manager Approval Inbox ({requests.length})
          </CardTitle>
        </div>
        <CardDescription className="text-xs text-textSecondary">
          Review pending team leave requests, inspect highlighted policy violations or coverage
          warnings, and approve or reject with an audit note.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <p className="py-8 text-center text-xs text-textSecondary">
            Loading manager approval inbox...
          </p>
        ) : requests.length === 0 ? (
          <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
            No pending leave requests in your inbox.
          </div>
        ) : (
          <div className="space-y-4">
            {requests.map((req) => {
              const allReasons = extractRuleReasons(req.policy_check_reasons);
              const violations = allReasons.filter(
                (r) => r.status === "VIOLATION" || r.status === "BLOCKED"
              );
              const isBusy = decideMutation.isPending && pendingId === req.id;

              return (
                <div
                  key={req.id}
                  className={`rounded-medium border p-4 space-y-3 ${
                    violations.length > 0
                      ? "border-amber-300 bg-amber-50/40"
                      : "border-appBorder bg-[#F8F9FD]"
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-bold text-[#102C54]">
                          {req.employee_name}
                        </span>
                        {req.department_name && (
                          <span className="rounded bg-white border border-appBorder px-2 py-0.5 text-[11px] font-medium text-textSecondary">
                            {req.department_name}
                          </span>
                        )}
                        <LeaveStatusBadge status={req.status} />
                        <PolicyCheckBadge status={req.policy_check_status} />
                      </div>
                      <p className="text-xs text-[#102C54] font-semibold">
                        {req.leave_type_name} &bull; {req.start_date} to {req.end_date} (
                        {formatDays(req.working_days)} working day(s))
                      </p>
                      <p className="text-xs text-textSecondary">{req.reason}</p>
                    </div>
                  </div>

                  {violations.length > 0 && (
                    <div className="rounded-small border border-amber-200 bg-amber-50 p-2.5 space-y-1">
                      <p className="text-[11px] font-bold text-amber-950 flex items-center gap-1.5">
                        <AlertTriangle className="h-3.5 w-3.5 text-amber-700" aria-hidden="true" />
                        Highlighted Policy Violations (Requires HR Exception):
                      </p>
                      <ul className="space-y-1 text-[11px] text-amber-950 list-disc pl-5">
                        {violations.map((v, i) => (
                          <li key={`${v.rule}-${i}`}>
                            <span className="font-semibold">{v.code}:</span> {v.message}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div className="flex flex-col sm:flex-row sm:items-center gap-2 pt-1">
                    <label htmlFor={`note-${req.id}`} className="sr-only">
                      Decision note for {req.employee_name}
                    </label>
                    <input
                      id={`note-${req.id}`}
                      type="text"
                      value={notesById[req.id] ?? ""}
                      onChange={(e) =>
                        setNotesById((prev) => ({ ...prev, [req.id]: e.target.value }))
                      }
                      placeholder="Optional approval / rejection note..."
                      disabled={decideMutation.isPending || isOffline}
                      className="flex-1 rounded-small border border-slate-300 bg-white px-3 py-1.5 text-xs text-[#102C54]"
                    />
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        disabled={decideMutation.isPending || isOffline}
                        onClick={() => void handleDecision(req, "approved")}
                        className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs inline-flex items-center gap-1"
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                        {isBusy ? "Saving..." : "Approve"}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={decideMutation.isPending || isOffline}
                        onClick={() => void handleDecision(req, "rejected")}
                        className="text-rose-700 border-rose-200 hover:bg-rose-50 text-xs inline-flex items-center gap-1"
                      >
                        <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
                        Reject
                      </Button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
