"use client";

import React, { useId, useState } from "react";
import { CalendarX2, FileText } from "lucide-react";
import type { LeaveRequestResponse } from "@agentic-hr/contracts";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  LeaveStatusBadge,
  PolicyCheckBadge,
  extractRuleReasons,
  formatDays,
} from "@/components/leave/leave-status-badges";
import { useCancelLeaveRequest } from "@/hooks/use-leave";
import { useToastStore } from "@/stores/toast-store";

interface MyRequestsTabProps {
  requests: LeaveRequestResponse[];
  isLoading: boolean;
  isOffline: boolean;
}

export function MyRequestsTab({ requests, isLoading, isOffline }: MyRequestsTabProps) {
  const captionId = useId();
  const [pendingCancelId, setPendingCancelId] = useState<string | null>(null);
  const cancelMutation = useCancelLeaveRequest();
  const pushToast = useToastStore((s) => s.pushToast);

  const handleCancel = async (req: LeaveRequestResponse) => {
    if (cancelMutation.isPending || isOffline) return;
    setPendingCancelId(req.id);
    try {
      const res = await cancelMutation.mutateAsync({
        requestId: req.id,
        payload: { reason: "Cancelled by employee" },
      });
      pushToast({
        variant: "success",
        title: "Leave Request Cancelled",
        description: `${res.leave_type_name} (${res.start_date} to ${res.end_date}) has been cancelled and balances updated.`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to cancel leave request.";
      pushToast({ variant: "error", title: "Cancellation Failed", description: msg });
    } finally {
      setPendingCancelId(null);
    }
  };

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader>
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
          <CardTitle className="text-base text-[#102C54]">
            My Leave Requests ({requests.length})
          </CardTitle>
        </div>
        <CardDescription className="text-xs text-textSecondary">
          Track pending approvals, policy exception gates, and cancel upcoming or approved leaves.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="py-8 text-center text-xs text-textSecondary">
            Loading your leave requests...
          </p>
        ) : requests.length === 0 ? (
          <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
            You have not submitted any leave requests yet.
          </div>
        ) : (
          <div
            tabIndex={0}
            role="region"
            aria-labelledby={captionId}
            className="overflow-auto rounded-medium border border-appBorder"
          >
            <table className="w-full min-w-[720px] border-separate border-spacing-0 text-left text-xs">
              <caption id={captionId} className="sr-only">
                My submitted leave requests
              </caption>
              <thead className="bg-[#F8F9FD] text-[11px] uppercase tracking-wide text-textSecondary">
                <tr>
                  <th scope="col" className="border-b border-appBorder px-3.5 py-2.5 font-semibold">
                    Leave Type
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2.5 font-semibold">
                    Dates &amp; Days
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2.5 font-semibold">
                    Policy Check
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2.5 font-semibold">
                    Status
                  </th>
                  <th
                    scope="col"
                    className="border-b border-appBorder px-3.5 py-2.5 font-semibold text-right"
                  >
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-appBorder bg-white">
                {requests.map((req) => {
                  const st = req.status.toLowerCase();
                  const isCancellable =
                    st === "pending" || st === "pending_hr_exception" || st === "approved";
                  const violationChips = extractRuleReasons(req.policy_check_reasons).filter(
                    (r) => r.status === "VIOLATION" || r.status === "BLOCKED"
                  );

                  return (
                    <tr key={req.id} className="hover:bg-[#F8F9FD]/60 align-top">
                      <th scope="row" className="px-3.5 py-3 font-normal text-left">
                        <span className="font-bold text-[#102C54] block">
                          {req.leave_type_name}
                        </span>
                        <span className="text-[11px] text-textSecondary block mt-0.5 max-w-[240px]">
                          {req.reason}
                        </span>
                      </th>
                      <td className="px-3 py-3 whitespace-nowrap">
                        <span className="font-semibold text-[#102C54] block">
                          {req.start_date} &rarr; {req.end_date}
                        </span>
                        <span className="text-[11px] text-[#2A5BDB] font-medium">
                          {formatDays(req.working_days)} day(s)
                          {req.is_half_day ? ` (${req.half_day_period || "half day"})` : ""}
                        </span>
                      </td>
                      <td className="px-3 py-3">
                        <div className="space-y-1">
                          <PolicyCheckBadge status={req.policy_check_status} />
                          {violationChips.map((chip, idx) => (
                            <span
                              key={`${chip.rule}-${idx}`}
                              className="block rounded-small border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-950"
                            >
                              {chip.message}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-3 py-3 whitespace-nowrap">
                        <div className="space-y-1">
                          <LeaveStatusBadge status={req.status} />
                          {req.decision_note && (
                            <p className="text-[10px] text-textSecondary max-w-[180px] truncate">
                              Note: {req.decision_note}
                            </p>
                          )}
                        </div>
                      </td>
                      <td className="px-3.5 py-3 text-right whitespace-nowrap">
                        {isCancellable ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={
                              cancelMutation.isPending || pendingCancelId === req.id || isOffline
                            }
                            onClick={() => void handleCancel(req)}
                            className="text-rose-700 border-rose-200 hover:bg-rose-50 text-[11px] inline-flex items-center gap-1"
                          >
                            <CalendarX2 className="h-3.5 w-3.5" aria-hidden="true" />
                            {pendingCancelId === req.id
                              ? "Cancelling..."
                              : st === "approved"
                                ? "Cancel Approved Leave"
                                : "Cancel Request"}
                          </Button>
                        ) : (
                          <span className="text-[11px] text-textMuted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
