"use client";

import React, { useId, useState } from "react";
import Link from "next/link";
import { AlertTriangle, BarChart3, ExternalLink } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  LeaveStatusBadge,
  PolicyCheckBadge,
  formatDays,
} from "@/components/leave/leave-status-badges";
import { useLeaveOverview } from "@/hooks/use-leave";

interface HrOverviewTabProps {
  canManage: boolean;
  isOffline: boolean;
}

export function HrOverviewTab({ canManage, isOffline }: HrOverviewTabProps) {
  const filterId = useId();
  const captionId = useId();
  const [statusFilter, setStatusFilter] = useState("");

  const { data: overview, isLoading } = useLeaveOverview(
    statusFilter || undefined,
    canManage && !isOffline
  );

  const requests = overview?.requests ?? [];
  const violationCount = requests.filter(
    (r) => (r.policy_check_status || "").toUpperCase() === "VIOLATION"
  ).length;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardContent className="pt-4">
            <span className="text-[11px] font-medium text-textSecondary">Total Requests</span>
            <p className="mt-1 text-2xl font-extrabold text-[#102C54]">
              {overview?.total_requests ?? 0}
            </p>
          </CardContent>
        </Card>
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardContent className="pt-4">
            <span className="text-[11px] font-medium text-textSecondary">Approved</span>
            <p className="mt-1 text-2xl font-extrabold text-emerald-700">
              {overview?.approved_count ?? 0}
            </p>
          </CardContent>
        </Card>
        <Card className="border border-amber-200 bg-amber-50/50 shadow-sm">
          <CardContent className="pt-4">
            <span className="text-[11px] font-medium text-amber-900">Pending HR Exceptions</span>
            <p className="mt-1 text-2xl font-extrabold text-amber-800">
              {overview?.pending_hr_exception_count ?? 0}
            </p>
          </CardContent>
        </Card>
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardContent className="pt-4">
            <span className="text-[11px] font-medium text-textSecondary">Policy Violations</span>
            <p className="mt-1 text-2xl font-extrabold text-[#2A5BDB]">{violationCount}</p>
          </CardContent>
        </Card>
      </div>

      {(overview?.pending_hr_exception_count ?? 0) > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-medium border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-950">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-700 shrink-0" aria-hidden="true" />
            <span>
              <strong>{overview?.pending_hr_exception_count} request(s)</strong> require a Stage 2
              HR Head exception decision (`leave.exception`) in Approvals Inbox.
            </span>
          </div>
          <Link
            href="/approvals"
            className="inline-flex items-center gap-1 rounded-small bg-[#102C54] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#2A5BDB]"
          >
            Open Approvals Inbox
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        </div>
      )}

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                <CardTitle className="text-base text-[#102C54]">
                  Organization Leave Utilization &amp; Audit Log
                </CardTitle>
              </div>
              <CardDescription className="text-xs text-textSecondary">
                Tenant-wide leave requests, Stage 2 exception statuses, and policy check flags.
              </CardDescription>
            </div>

            <div role="search" aria-label="Filter tenant leave requests" className="w-48">
              <label htmlFor={filterId} className="sr-only">
                Filter by status
              </label>
              <select
                id={filterId}
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="w-full rounded-small border border-appBorder bg-[#F8F9FD] px-2.5 py-1.5 text-xs text-[#102C54]"
              >
                <option value="">All Statuses</option>
                <option value="pending">Pending</option>
                <option value="pending_hr_exception">Pending HR Exception</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
                <option value="cancelled">Cancelled</option>
                <option value="expired">Expired</option>
              </select>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="py-8 text-center text-xs text-textSecondary">
              Loading organization leave overview...
            </p>
          ) : requests.length === 0 ? (
            <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
              No leave requests match this status filter.
            </div>
          ) : (
            <div
              tabIndex={0}
              role="region"
              aria-labelledby={captionId}
              className="overflow-auto rounded-medium border border-appBorder"
            >
              <table className="w-full min-w-[740px] border-separate border-spacing-0 text-left text-xs">
                <caption id={captionId} className="sr-only">
                  Organization leave utilization and policy exceptions
                </caption>
                <thead className="bg-[#F8F9FD] text-[11px] uppercase tracking-wide text-textSecondary">
                  <tr>
                    <th
                      scope="col"
                      className="border-b border-appBorder px-3.5 py-2.5 font-semibold"
                    >
                      Employee
                    </th>
                    <th scope="col" className="border-b border-appBorder px-3 py-2.5 font-semibold">
                      Leave Type &amp; Dates
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
                      HR Gate
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-appBorder bg-white">
                  {requests.map((req) => (
                    <tr key={req.id} className="hover:bg-[#F8F9FD]/60 align-top">
                      <th scope="row" className="px-3.5 py-3 font-normal text-left">
                        <span className="font-bold text-[#102C54] block">{req.employee_name}</span>
                        <span className="text-[11px] text-textSecondary">
                          {req.department_name || "Unassigned"}
                        </span>
                      </th>
                      <td className="px-3 py-3 whitespace-nowrap">
                        <span className="font-semibold text-[#102C54] block">
                          {req.leave_type_name} ({formatDays(req.working_days)}d)
                        </span>
                        <span className="text-[11px] text-textSecondary">
                          {req.start_date} &rarr; {req.end_date}
                        </span>
                      </td>
                      <td className="px-3 py-3">
                        <PolicyCheckBadge status={req.policy_check_status} />
                      </td>
                      <td className="px-3 py-3 whitespace-nowrap">
                        <LeaveStatusBadge status={req.status} />
                      </td>
                      <td className="px-3.5 py-3 text-right whitespace-nowrap">
                        {req.hr_exception_approval_id ? (
                          <Link
                            href="/approvals"
                            className="inline-flex items-center gap-1 font-semibold text-[#2A5BDB] hover:underline"
                          >
                            Review Exception
                            <ExternalLink className="h-3 w-3" aria-hidden="true" />
                          </Link>
                        ) : (
                          <span className="text-textMuted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
