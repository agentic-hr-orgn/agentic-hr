"use client";

import React, { useEffect, useId, useState } from "react";
import { Lock, Save, Sparkles, Users } from "lucide-react";
import type { DailyAttendanceUpsertItem } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ATTENDANCE_STATUSES,
  ATTENDANCE_STATUS_LABELS,
  AttendanceStatusBadge,
  type AttendanceStatusOption,
} from "@/components/attendance/attendance-status-badges";
import { PostLockEditModal } from "@/components/attendance/post-lock-edit-modal";
import {
  useAutoFillMonthAttendance,
  useDailyAttendanceGrid,
  useUpsertDailyAttendanceGrid,
} from "@/hooks/use-attendance";
import { useDepartments } from "@/hooks/use-departments";
import { getTodayPKTDateString } from "@/lib/format";
import { useToastStore } from "@/stores/toast-store";

interface DailyGridTabProps {
  canAccess: boolean;
  canManage: boolean;
  isOffline: boolean;
}

function getMonthRange(ym: string): { start: string; end: string } {
  const [yStr, mStr] = ym.split("-");
  const y = Number(yStr) || 2026;
  const m = Number(mStr) || 11;
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const mm = String(m).padStart(2, "0");
  return { start: `${y}-${mm}-01`, end: `${y}-${mm}-${String(days).padStart(2, "0")}` };
}

export function DailyGridTab({ canAccess, canManage, isOffline }: DailyGridTabProps) {
  const dateInputId = useId();
  const deptSelectId = useId();
  const tableCaptionId = useId();

  // Deterministic SSR initial date synced to current PKT date after mount
  const [targetDate, setTargetDate] = useState("2026-11-22");
  useEffect(() => {
    setTargetDate(getTodayPKTDateString());
  }, []);

  const [departmentId, setDepartmentId] = useState("");
  const [statusDrafts, setStatusDrafts] = useState<Record<string, AttendanceStatusOption>>({});
  const [notesDrafts, setNotesDrafts] = useState<Record<string, string>>({});
  const [showPostLockModal, setShowPostLockModal] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data: departments = [] } = useDepartments();
  const {
    data: grid,
    isLoading,
    error: gridError,
  } = useDailyAttendanceGrid(targetDate, departmentId || undefined, canAccess && !isOffline);

  const upsertMutation = useUpsertDailyAttendanceGrid();
  const autoFillMutation = useAutoFillMonthAttendance();
  const pushToast = useToastStore((s) => s.pushToast);

  // Refinement 2: Clear unsaved local row edits whenever targetDate or departmentId changes
  useEffect(() => {
    setStatusDrafts({});
    setNotesDrafts({});
    setActionError(null);
  }, [targetDate, departmentId]);

  const yearMonth = grid?.year_month || targetDate.slice(0, 7);
  const isLocked = Boolean(grid?.is_locked);
  const monthBounds = getMonthRange(yearMonth);

  const handleSaveGrid = async () => {
    if (!grid || isLocked || upsertMutation.isPending || isOffline) return;
    setActionError(null);

    const entries: DailyAttendanceUpsertItem[] = [];
    for (const row of grid.rows) {
      const hasStatusDraft = Object.prototype.hasOwnProperty.call(statusDrafts, row.employee_id);
      const hasNotesDraft = Object.prototype.hasOwnProperty.call(notesDrafts, row.employee_id);
      const effectiveStatus = (
        hasStatusDraft ? statusDrafts[row.employee_id] : row.status
      ) as AttendanceStatusOption | null;

      if (!effectiveStatus) continue;
      if (hasStatusDraft || hasNotesDraft || row.status !== null) {
        const rawNotes = hasNotesDraft ? notesDrafts[row.employee_id] : (row.notes ?? "");
        const trimmedNotes = rawNotes.trim();
        entries.push({
          employee_id: row.employee_id,
          status: effectiveStatus,
          ...(trimmedNotes ? { notes: trimmedNotes } : {}),
        });
      }
    }

    if (entries.length === 0) {
      setActionError("Select at least one employee attendance status before saving.");
      return;
    }

    try {
      const res = await upsertMutation.mutateAsync({
        target_date: targetDate,
        entries,
      });
      setStatusDrafts({});
      setNotesDrafts({});
      pushToast({
        variant: "success",
        title: "Daily Grid Saved",
        description: `Saved ${res.upserted_count} attendance record(s) for ${res.target_date}.`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save daily attendance grid.";
      setActionError(msg);
      pushToast({ variant: "error", title: "Save Daily Grid Failed", description: msg });
    }
  };

  const handleAutoFillMonth = async () => {
    if (!canManage || isLocked || autoFillMutation.isPending || isOffline) return;
    setActionError(null);
    try {
      const res = await autoFillMutation.mutateAsync({
        yearMonth,
        payload: { overwrite_existing: false },
      });
      pushToast({
        variant: "success",
        title: `Auto-Filled ${res.year_month}`,
        description: `Populated ${res.total_filled} day(s) (${res.filled_weekend_days} weekends, ${res.filled_holiday_days} holidays, ${res.filled_leave_days} leave days).`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to auto-fill attendance month.";
      setActionError(msg);
      pushToast({ variant: "error", title: "Auto-Fill Failed", description: msg });
    }
  };

  return (
    <div className="space-y-4">
      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Users className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              <CardTitle className="text-base text-[#102C54]">
                Manager Daily Attendance Grid ({grid?.total_employees ?? 0})
              </CardTitle>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {isLocked ? (
                <Badge variant="warning" className="inline-flex items-center gap-1">
                  <Lock className="h-3 w-3" aria-hidden="true" />
                  {yearMonth} Locked
                </Badge>
              ) : (
                <Badge variant="success">{yearMonth} Open</Badge>
              )}
              {grid && (
                <Badge variant="outline">
                  Marked: {grid.marked_count} / {grid.total_employees}
                </Badge>
              )}
            </div>
          </div>
          <CardDescription className="text-xs text-textSecondary">
            Mark daily statuses for your team (`manager_grid`). Direct edits are blocked when a
            month is locked for payroll.
          </CardDescription>

          <div
            role="search"
            aria-label="Filter daily attendance grid"
            className="flex flex-wrap items-end justify-between gap-3 pt-1"
          >
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label
                  htmlFor={dateInputId}
                  className="block text-[11px] font-semibold text-[#102C54] mb-1"
                >
                  Target Date
                </label>
                <input
                  id={dateInputId}
                  type="date"
                  value={targetDate}
                  onChange={(e) => setTargetDate(e.target.value)}
                  className="rounded-small border border-appBorder bg-[#F8F9FD] px-2.5 py-1.5 text-xs text-[#102C54]"
                />
              </div>

              <div>
                <label
                  htmlFor={deptSelectId}
                  className="block text-[11px] font-semibold text-[#102C54] mb-1"
                >
                  Department
                </label>
                <select
                  id={deptSelectId}
                  value={departmentId}
                  onChange={(e) => setDepartmentId(e.target.value)}
                  className="rounded-small border border-appBorder bg-[#F8F9FD] px-2.5 py-1.5 text-xs text-[#102C54]"
                >
                  <option value="">All Departments</option>
                  {departments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {canManage && !isLocked && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={autoFillMutation.isPending || isOffline}
                  onClick={() => void handleAutoFillMonth()}
                  className="border-[#2A5BDB]/30 text-[#2A5BDB] hover:bg-[#F8F9FD] text-xs inline-flex items-center gap-1.5"
                >
                  <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                  {autoFillMutation.isPending
                    ? "Auto-Filling..."
                    : "Auto-Fill Weekends/Holidays/Leave"}
                </Button>
              )}

              {isLocked ? (
                <Button
                  type="button"
                  size="sm"
                  disabled={!grid || grid.rows.length === 0 || isOffline}
                  onClick={() => setShowPostLockModal(true)}
                  className="bg-amber-600 hover:bg-amber-700 text-white text-xs inline-flex items-center gap-1.5"
                >
                  <Lock className="h-3.5 w-3.5" aria-hidden="true" />
                  Request Post-Lock Edit
                </Button>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  disabled={
                    upsertMutation.isPending || isOffline || !grid || grid.rows.length === 0
                  }
                  onClick={() => void handleSaveGrid()}
                  className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs inline-flex items-center gap-1.5"
                >
                  <Save className="h-3.5 w-3.5" aria-hidden="true" />
                  {upsertMutation.isPending ? "Saving Grid..." : "Save Daily Grid"}
                </Button>
              )}
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          {(actionError || gridError) && (
            <div
              role="alert"
              className="rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs font-medium text-rose-900"
            >
              {actionError || gridError?.message}
            </div>
          )}

          {isLoading ? (
            <p className="py-8 text-center text-xs text-textSecondary">
              Loading daily attendance grid...
            </p>
          ) : !grid || grid.rows.length === 0 ? (
            <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
              No active employees found for this filter.
            </div>
          ) : (
            <div
              tabIndex={0}
              role="region"
              aria-labelledby={tableCaptionId}
              className="overflow-auto rounded-medium border border-appBorder"
            >
              <table className="w-full min-w-[740px] border-separate border-spacing-0 text-left text-xs">
                <caption id={tableCaptionId} className="sr-only">
                  Daily attendance grid for {targetDate}
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
                      Department
                    </th>
                    <th scope="col" className="border-b border-appBorder px-3 py-2.5 font-semibold">
                      Current Status
                    </th>
                    <th scope="col" className="border-b border-appBorder px-3 py-2.5 font-semibold">
                      Mark Status
                    </th>
                    <th
                      scope="col"
                      className="border-b border-appBorder px-3.5 py-2.5 font-semibold"
                    >
                      Notes
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-appBorder bg-white">
                  {grid.rows.map((row) => {
                    const draftStatus = statusDrafts[row.employee_id] ?? row.status ?? "";
                    const draftNotes = notesDrafts[row.employee_id] ?? row.notes ?? "";
                    const rowLocked = isLocked || row.locked;

                    return (
                      <tr key={row.employee_id} className="hover:bg-[#F8F9FD]/60">
                        <th scope="row" className="px-3.5 py-2.5 font-normal text-left">
                          <span className="font-bold text-[#102C54] block">
                            {row.employee_name}
                          </span>
                          <span className="text-[11px] text-textSecondary">
                            {row.employee_email}
                          </span>
                        </th>
                        <td className="px-3 py-2.5 whitespace-nowrap text-textSecondary">
                          {row.department_name || "—"}
                        </td>
                        <td className="px-3 py-2.5 whitespace-nowrap">
                          <div className="flex items-center gap-1.5">
                            <AttendanceStatusBadge status={row.status} />
                            {row.source && (
                              <span className="text-[10px] font-mono text-textMuted">
                                ({row.source})
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 whitespace-nowrap">
                          <select
                            aria-label={`Attendance status for ${row.employee_name}`}
                            value={draftStatus}
                            disabled={rowLocked || upsertMutation.isPending}
                            onChange={(e) => {
                              const val = e.target.value as AttendanceStatusOption | "";
                              if (!val) return;
                              setStatusDrafts((prev) => ({ ...prev, [row.employee_id]: val }));
                            }}
                            className="rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-[#102C54] disabled:bg-slate-100"
                          >
                            <option value="" disabled>
                              Select status...
                            </option>
                            {ATTENDANCE_STATUSES.map((st) => (
                              <option key={st} value={st}>
                                {ATTENDANCE_STATUS_LABELS[st]}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="px-3.5 py-2.5">
                          <input
                            type="text"
                            aria-label={`Attendance notes for ${row.employee_name}`}
                            value={draftNotes}
                            disabled={rowLocked || upsertMutation.isPending}
                            onChange={(e) =>
                              setNotesDrafts((prev) => ({
                                ...prev,
                                [row.employee_id]: e.target.value,
                              }))
                            }
                            placeholder="Optional notes"
                            className="w-full min-w-[160px] rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-[#102C54] disabled:bg-slate-100"
                          />
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

      {showPostLockModal && grid && (
        <PostLockEditModal
          yearMonth={yearMonth}
          monthStart={monthBounds.start}
          monthEnd={monthBounds.end}
          employees={grid.rows.map((r) => ({
            employee_id: r.employee_id,
            employee_name: r.employee_name,
            employee_email: r.employee_email,
          }))}
          defaultDate={targetDate}
          isOffline={isOffline}
          onClose={() => setShowPostLockModal(false)}
        />
      )}
    </div>
  );
}
