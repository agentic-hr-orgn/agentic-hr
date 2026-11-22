"use client";

import React, { useEffect, useId, useState } from "react";
import { AlertTriangle, CheckCircle2, Lock, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PostLockEditModal } from "@/components/attendance/post-lock-edit-modal";
import {
  useAutoFillMonthAttendance,
  useLockMonthAttendance,
  useMonthAttendanceReview,
} from "@/hooks/use-attendance";
import { formatPKT, getTodayPKTDateString } from "@/lib/format";
import { useToastStore } from "@/stores/toast-store";

interface MonthReviewTabProps {
  canManage: boolean;
  isOffline: boolean;
}

export function MonthReviewTab({ canManage, isOffline }: MonthReviewTabProps) {
  const monthInputId = useId();
  const tableCaptionId = useId();

  // Deterministic SSR initial month synced after mount to prevent hydration #418
  const [yearMonth, setYearMonth] = useState("2026-11");
  useEffect(() => {
    setYearMonth(getTodayPKTDateString().slice(0, 7));
  }, []);

  const [showPostLockModal, setShowPostLockModal] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const {
    data: review,
    isLoading,
    error: reviewError,
  } = useMonthAttendanceReview(yearMonth, canManage && !isOffline);

  const autoFillMutation = useAutoFillMonthAttendance();
  const lockMutation = useLockMonthAttendance();
  const pushToast = useToastStore((s) => s.pushToast);

  const handleAutoFill = async () => {
    if (!canManage || review?.is_locked || autoFillMutation.isPending || isOffline) return;
    setActionError(null);
    try {
      const res = await autoFillMutation.mutateAsync({
        yearMonth,
        payload: { overwrite_existing: false },
      });
      pushToast({
        variant: "success",
        title: `Auto-Filled ${res.year_month}`,
        description: `Filled ${res.total_filled} day(s) (${res.filled_weekend_days} weekends, ${res.filled_holiday_days} holidays, ${res.filled_leave_days} leave days).`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Auto-fill failed.";
      setActionError(msg);
      pushToast({ variant: "error", title: "Auto-Fill Failed", description: msg });
    }
  };

  const handleLockMonth = async () => {
    if (!canManage || !review || review.is_locked || lockMutation.isPending || isOffline) return;
    setActionError(null);
    try {
      const res = await lockMutation.mutateAsync(yearMonth);
      pushToast({
        variant: "success",
        title: `Locked ${res.year_month}`,
        description: `Payroll lock applied across ${res.employee_count} employee(s) and ${res.record_count} record(s).`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to lock attendance month.";
      setActionError(msg);
      pushToast({ variant: "error", title: "Month Lock Refused", description: msg });
    }
  };

  const totalGaps = review?.total_gaps ?? 0;
  const isLocked = Boolean(review?.is_locked);
  const canLock = Boolean(review?.can_lock) && totalGaps === 0 && !isLocked && !isOffline;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardContent className="pt-4">
            <span className="text-[11px] font-semibold uppercase text-textSecondary">
              Active Employees
            </span>
            <p className="mt-1 text-2xl font-extrabold text-[#102C54]">
              {review?.active_employees ?? 0}
            </p>
          </CardContent>
        </Card>
        <Card
          className={`border shadow-sm ${
            totalGaps > 0
              ? "border-amber-200 bg-amber-50/50"
              : "border-emerald-200 bg-emerald-50/50"
          }`}
        >
          <CardContent className="pt-4">
            <span className="text-[11px] font-semibold uppercase text-textSecondary">
              Missing Status Gaps
            </span>
            <p
              className={`mt-1 text-2xl font-extrabold ${
                totalGaps > 0 ? "text-amber-800" : "text-emerald-700"
              }`}
            >
              {totalGaps}
            </p>
          </CardContent>
        </Card>
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardContent className="pt-4">
            <span className="text-[11px] font-semibold uppercase text-textSecondary">
              Payroll Month Status
            </span>
            <div className="mt-2">
              {isLocked ? (
                <Badge variant="warning" className="inline-flex items-center gap-1">
                  <Lock className="h-3 w-3" aria-hidden="true" />
                  Locked
                </Badge>
              ) : (
                <Badge variant="success">Unlocked / Open</Badge>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {isLocked && review && (
        <div
          role="status"
          aria-live="polite"
          className="flex flex-wrap items-center justify-between gap-3 rounded-medium border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-950"
        >
          <div className="flex items-center gap-2">
            <Lock className="h-4 w-4 text-amber-700 shrink-0" aria-hidden="true" />
            <span>
              <strong>{review.year_month} is Locked for Payroll</strong>
              {review.locked_by ? ` (Locked by ${review.locked_by}` : " ("}
              {review.locked_at ? ` at ${formatPKT(review.locked_at)})` : ")"}. Direct edits are
              blocked.
            </span>
          </div>
          <Button
            type="button"
            size="sm"
            disabled={review.employees.length === 0 || isOffline}
            onClick={() => setShowPostLockModal(true)}
            className="bg-[#102C54] text-white hover:bg-[#2A5BDB] text-xs"
          >
            Request Post-Lock Edit
          </Button>
        </div>
      )}

      {!isLocked && totalGaps > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-medium border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-950">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-700 shrink-0" aria-hidden="true" />
            <span>
              <strong>Cannot Lock Month:</strong> {totalGaps} attendance gap(s) remain across active
              employees. Run Auto-Fill or fill missing working days first.
            </span>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={autoFillMutation.isPending || isOffline}
            onClick={() => void handleAutoFill()}
            className="border-amber-300 bg-white text-amber-950 hover:bg-amber-100 text-xs inline-flex items-center gap-1.5"
          >
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            {autoFillMutation.isPending
              ? "Auto-Filling..."
              : "Auto-Fill Weekends/Holidays/ON_LEAVE"}
          </Button>
        </div>
      )}

      {(actionError || reviewError) && (
        <div
          role="alert"
          className="rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs font-medium text-rose-900"
        >
          {actionError || reviewError?.message}
        </div>
      )}

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <CardTitle className="text-base text-[#102C54]">
                Month Completeness Review &amp; Lock ({yearMonth})
              </CardTitle>
              <CardDescription className="text-xs text-textSecondary">
                Verify every active employee has an attendance status on every calendar day prior to
                locking payroll.
              </CardDescription>
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <div role="search" aria-label="Select month for review">
                <label
                  htmlFor={monthInputId}
                  className="block text-[11px] font-semibold text-[#102C54] mb-1"
                >
                  Review Month
                </label>
                <input
                  id={monthInputId}
                  type="month"
                  value={yearMonth}
                  onChange={(e) => {
                    setActionError(null);
                    setYearMonth(e.target.value);
                  }}
                  className="rounded-small border border-appBorder bg-[#F8F9FD] px-2.5 py-1.5 text-xs text-[#102C54]"
                />
              </div>

              {!isLocked && (
                <Button
                  type="button"
                  size="sm"
                  disabled={!canLock || lockMutation.isPending}
                  onClick={() => void handleLockMonth()}
                  className="bg-[#102C54] text-white hover:bg-[#2A5BDB] text-xs inline-flex items-center gap-1.5"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                  {lockMutation.isPending ? "Locking Month..." : "Lock Month"}
                </Button>
              )}
            </div>
          </div>
        </CardHeader>

        <CardContent>
          {isLoading ? (
            <p className="py-8 text-center text-xs text-textSecondary">
              Computing month attendance review...
            </p>
          ) : !review || review.employees.length === 0 ? (
            <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
              No active employees eligible for {yearMonth}.
            </div>
          ) : (
            <div
              tabIndex={0}
              role="region"
              aria-labelledby={tableCaptionId}
              className="overflow-auto rounded-medium border border-appBorder"
            >
              <table className="w-full min-w-[780px] border-separate border-spacing-0 text-left text-xs">
                <caption id={tableCaptionId} className="sr-only">
                  Monthly attendance review and missing dates for {yearMonth}
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
                      Summary
                    </th>
                    <th scope="col" className="border-b border-appBorder px-3 py-2.5 font-semibold">
                      Recorded
                    </th>
                    <th
                      scope="col"
                      className="border-b border-appBorder px-3.5 py-2.5 font-semibold"
                    >
                      Days With No Status (Gaps)
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-appBorder bg-white">
                  {review.employees.map((emp) => (
                    <tr key={emp.employee_id} className="hover:bg-[#F8F9FD]/60 align-top">
                      <th scope="row" className="px-3.5 py-2.5 font-normal text-left">
                        <span className="font-bold text-[#102C54] block">{emp.employee_name}</span>
                        <span className="text-[11px] text-textSecondary">
                          {emp.department_name || emp.employee_email}
                        </span>
                      </th>
                      <td className="px-3 py-2.5 whitespace-nowrap text-[11px] text-textSecondary">
                        P:{emp.present_days} &bull; WFH:{emp.wfh_days} &bull; L:{emp.on_leave_days}{" "}
                        &bull; H:{emp.holiday_days} &bull; W:{emp.weekend_days}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap font-semibold text-[#102C54]">
                        {emp.recorded_days} / {review.days_in_month}
                      </td>
                      <td className="px-3.5 py-2.5">
                        {(emp.missing_days ?? []).length === 0 ? (
                          <Badge variant="success" className="text-[10px]">
                            0 Gaps
                          </Badge>
                        ) : (
                          <div className="flex flex-wrap gap-1">
                            {(emp.missing_days ?? []).map((d) => (
                              <span
                                key={d}
                                className="inline-flex items-center rounded border border-rose-200 bg-rose-50 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-rose-800"
                              >
                                {d}
                              </span>
                            ))}
                          </div>
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

      {showPostLockModal && review && (
        <PostLockEditModal
          yearMonth={review.year_month}
          monthStart={review.month_start}
          monthEnd={review.month_end}
          employees={review.employees.map((e) => ({
            employee_id: e.employee_id,
            employee_name: e.employee_name,
            employee_email: e.employee_email,
          }))}
          isOffline={isOffline}
          onClose={() => setShowPostLockModal(false)}
        />
      )}
    </div>
  );
}
