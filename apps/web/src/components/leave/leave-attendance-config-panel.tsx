"use client";

import React, { useEffect, useId, useState } from "react";
import { CalendarClock, CheckCircle2, Plus, Save, Trash2, XCircle } from "lucide-react";
import type { LeaveTypeResponse } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDays, toNumericDays } from "@/components/leave/leave-status-badges";
import { useEmployees } from "@/hooks/use-employees";
import {
  useCreateHoliday,
  useCreateLeaveType,
  useDeleteHoliday,
  useDeleteLeaveType,
  useHolidays,
  useLeaveTypes,
  useUpdateHoliday,
  useUpdateLeaveType,
  useUpdateWorkWeek,
  useWorkWeek,
} from "@/hooks/use-leave";
import { useToastStore } from "@/stores/toast-store";

export interface LeaveConfigWizardSnapshot {
  weekends: string[];
  probation_months: number;
  check_in_enabled: boolean;
  leave_rules: Record<string, unknown>;
  holidays: Array<{ date: string; name: string }>;
}

interface LeaveAttendanceConfigPanelProps {
  mode?: "settings" | "wizard";
  canManage: boolean;
  isOffline?: boolean;
  onConfigSnapshotChange?: (snapshot: LeaveConfigWizardSnapshot) => void;
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function LeaveAttendanceConfigPanel({
  mode = "settings",
  canManage,
  isOffline = false,
  onConfigSnapshotChange,
}: LeaveAttendanceConfigPanelProps) {
  const probationId = useId();
  const maxAbsentId = useId();
  const minPresentId = useId();
  const backupApproverId = useId();
  const holidayYearId = useId();
  const newHolidayDateId = useId();
  const newHolidayNameId = useId();
  const newLtCodeId = useId();
  const newLtNameId = useId();
  const newLtDaysId = useId();

  // Deterministic SSR year synced to actual client year after mount (prevents React hydration #418)
  const [holidayYear, setHolidayYear] = useState<number>(2026);
  useEffect(() => {
    setHolidayYear(new Date().getFullYear());
  }, []);

  const { data: workWeek, error: wwError } = useWorkWeek(!isOffline);
  const { data: leaveTypes = [] } = useLeaveTypes(false, !isOffline);
  const { data: holidays = [] } = useHolidays(holidayYear, !isOffline);

  // Refinement 4: Gate backup approver directory fetch on canManage so viewers never fire 403s
  const { data: employeeList } = useEmployees({ pageSize: 100, enabled: canManage && !isOffline });

  const updateWorkWeek = useUpdateWorkWeek();
  const createLeaveType = useCreateLeaveType();
  const updateLeaveType = useUpdateLeaveType();
  const deleteLeaveType = useDeleteLeaveType();
  const createHoliday = useCreateHoliday();
  const updateHoliday = useUpdateHoliday();
  const deleteHoliday = useDeleteHoliday();
  const pushToast = useToastStore((s) => s.pushToast);

  const [weekendDays, setWeekendDays] = useState<string[]>(["Saturday", "Sunday"]);
  const [probationMonths, setProbationMonths] = useState<number>(3);
  const [maxTeamAbsentPct, setMaxTeamAbsentPct] = useState<string>("30");
  const [minTeamPresent, setMinTeamPresent] = useState<number>(1);
  const [backupApproverUserId, setBackupApproverUserId] = useState<string>("");
  const [checkInEnabled, setCheckInEnabled] = useState<boolean>(true);
  const [localError, setLocalError] = useState<string | null>(null);
  const [localSuccess, setLocalSuccess] = useState<string | null>(null);

  // Add Leave Type inline form
  const [newLtCode, setNewLtCode] = useState("");
  const [newLtName, setNewLtName] = useState("");
  const [newLtDays, setNewLtDays] = useState("10");
  const [newLtAccrual, setNewLtAccrual] = useState<"upfront" | "monthly">("upfront");
  const [newLtCarry, setNewLtCarry] = useState("0");

  // Add Holiday inline form
  const [newHolidayDate, setNewHolidayDate] = useState("");
  const [newHolidayName, setNewHolidayName] = useState("");
  const [newHolidayOptional, setNewHolidayOptional] = useState(false);

  // Editing Holiday inline state
  const [editingHolidayId, setEditingHolidayId] = useState<string | null>(null);
  const [editHolidayName, setEditHolidayName] = useState("");

  useEffect(() => {
    if (!workWeek) return;
    setWeekendDays(
      Array.isArray(workWeek.weekend_days) && workWeek.weekend_days.length > 0
        ? workWeek.weekend_days
        : ["Saturday", "Sunday"]
    );
    setProbationMonths(workWeek.probation_months ?? 3);
    setMaxTeamAbsentPct(String(workWeek.max_team_absent_pct ?? "30"));
    setMinTeamPresent(workWeek.min_team_present ?? 1);
    setBackupApproverUserId(workWeek.backup_approver_user_id ?? "");
    setCheckInEnabled(workWeek.check_in_enabled ?? true);
  }, [workWeek]);

  useEffect(() => {
    if (!onConfigSnapshotChange) return;
    const byCode: Record<string, number> = {};
    let maxCarry = 0;
    for (const lt of leaveTypes) {
      byCode[`${lt.code}_days`] = toNumericDays(lt.annual_allowance_days);
      const cf = toNumericDays(lt.carry_forward_cap_days);
      if (cf > maxCarry) maxCarry = cf;
    }
    onConfigSnapshotChange({
      weekends: weekendDays,
      probation_months: probationMonths,
      check_in_enabled: checkInEnabled,
      leave_rules: {
        ...byCode,
        carry_forward_max: maxCarry,
      },
      holidays: holidays.map((h) => ({ date: h.holiday_date, name: h.name })),
    });
  }, [weekendDays, probationMonths, checkInEnabled, leaveTypes, holidays, onConfigSnapshotChange]);

  const backupCandidates = (employeeList?.items ?? []).filter((emp) => Boolean(emp.user_id));

  const toggleWeekendDay = (day: string) => {
    if (!canManage) return;
    setWeekendDays((prev) => {
      if (prev.includes(day)) {
        return prev.length > 1 ? prev.filter((d) => d !== day) : prev;
      }
      return [...prev, day];
    });
  };

  const handleSaveWorkWeek = async () => {
    if (!canManage || updateWorkWeek.isPending || isOffline) return;
    setLocalError(null);
    setLocalSuccess(null);
    try {
      await updateWorkWeek.mutateAsync({
        weekend_days: weekendDays,
        probation_months: probationMonths,
        max_team_absent_pct: maxTeamAbsentPct,
        min_team_present: minTeamPresent,
        backup_approver_user_id: backupApproverUserId ? backupApproverUserId : null,
        check_in_enabled: checkInEnabled,
      });
      setLocalSuccess("Saved work-week, probation, team coverage, and check-in rules.");
      pushToast({
        variant: "success",
        title: "Work-Week Rules Saved",
        description: "Updated weekend schedule, probation, team coverage & check-in toggle.",
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to save work-week rules.";
      setLocalError(msg);
      pushToast({ variant: "error", title: "Save Failed", description: msg });
    }
  };

  const handleQuickUpdateAllowance = async (lt: LeaveTypeResponse, nextDays: number) => {
    if (!canManage || updateLeaveType.isPending || isOffline) return;
    setLocalError(null);
    try {
      await updateLeaveType.mutateAsync({
        leaveTypeId: lt.id,
        payload: { annual_allowance_days: nextDays },
      });
      setLocalSuccess(`Updated '${lt.name}' allowance to ${nextDays} day(s).`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update leave type.";
      setLocalError(msg);
    }
  };

  const handleAddLeaveType = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canManage || createLeaveType.isPending || isOffline) return;
    if (!newLtCode.trim() || !newLtName.trim()) {
      setLocalError("Leave type code and name are required.");
      return;
    }
    setLocalError(null);
    setLocalSuccess(null);
    try {
      const created = await createLeaveType.mutateAsync({
        code: newLtCode.trim().toLowerCase(),
        name: newLtName.trim(),
        annual_allowance_days: Number(newLtDays) || 0,
        accrual_mode: newLtAccrual,
        carry_forward_cap_days: Number(newLtCarry) || 0,
        min_notice_days: 0,
        allow_during_probation: false,
        allow_half_day: true,
        is_paid: true,
        is_active: true,
      });
      setNewLtCode("");
      setNewLtName("");
      setLocalSuccess(`Created leave type '${created.name}'.`);
    } catch (err: unknown) {
      setLocalError(err instanceof Error ? err.message : "Failed to create leave type.");
    }
  };

  const handleDeleteLeaveType = async (lt: LeaveTypeResponse) => {
    if (!canManage || deleteLeaveType.isPending || isOffline) return;
    setLocalError(null);
    try {
      await deleteLeaveType.mutateAsync(lt.id);
      setLocalSuccess(`Removed/deactivated leave type '${lt.name}'.`);
    } catch (err: unknown) {
      setLocalError(err instanceof Error ? err.message : "Failed to delete leave type.");
    }
  };

  const handleAddHoliday = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canManage || createHoliday.isPending || isOffline) return;
    if (!newHolidayDate || !newHolidayName.trim()) {
      setLocalError("Holiday date and name are required.");
      return;
    }
    setLocalError(null);
    setLocalSuccess(null);
    try {
      const created = await createHoliday.mutateAsync({
        holiday_date: newHolidayDate,
        name: newHolidayName.trim(),
        is_optional: newHolidayOptional,
        source: "custom",
      });
      setNewHolidayDate("");
      setNewHolidayName("");
      setNewHolidayOptional(false);
      setLocalSuccess(`Added holiday '${created.name}' (${created.holiday_date}).`);
    } catch (err: unknown) {
      setLocalError(err instanceof Error ? err.message : "Failed to add holiday.");
    }
  };

  const handleSaveHolidayEdit = async (holidayId: string) => {
    if (!canManage || updateHoliday.isPending || isOffline || !editHolidayName.trim()) return;
    setLocalError(null);
    try {
      await updateHoliday.mutateAsync({
        holidayId,
        payload: { name: editHolidayName.trim() },
      });
      setEditingHolidayId(null);
      setLocalSuccess("Holiday updated.");
    } catch (err: unknown) {
      setLocalError(err instanceof Error ? err.message : "Failed to update holiday.");
    }
  };

  const handleDeleteHoliday = async (holidayId: string, name: string) => {
    if (!canManage || deleteHoliday.isPending || isOffline) return;
    setLocalError(null);
    try {
      await deleteHoliday.mutateAsync(holidayId);
      setLocalSuccess(`Deleted holiday '${name}'.`);
    } catch (err: unknown) {
      setLocalError(err instanceof Error ? err.message : "Failed to delete holiday.");
    }
  };

  return (
    <div className="space-y-6">
      {(localError || wwError) && (
        <div
          role="alert"
          className="flex items-center justify-between gap-2 rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{localError || wwError?.message}</span>
          </div>
          {localError && (
            <button
              type="button"
              onClick={() => setLocalError(null)}
              className="font-bold text-rose-700 hover:text-rose-900 px-1"
              aria-label="Dismiss config error"
            >
              &times;
            </button>
          )}
        </div>
      )}

      {localSuccess && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 rounded-medium border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-medium text-emerald-800"
        >
          <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
          <span>{localSuccess}</span>
        </div>
      )}

      {/* 1. Work-Week, Probation, Coverage & Check-In Rules */}
      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <CalendarClock className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              <CardTitle className="text-base text-[#102C54]">
                Work-Week, Probation &amp; Attendance Rules
              </CardTitle>
            </div>
            <Badge variant="success">
              {mode === "wizard" ? "Stage 6 — Live" : "Postgres Authoritative"}
            </Badge>
          </div>
          <CardDescription className="text-xs text-textSecondary">
            Configure Weekend Schedule, employee self check-in toggle, probation window, team
            coverage guardrails, and escalation backup approver.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-xs">
          <div>
            <p className="font-semibold text-[#102C54] mb-1.5">Weekend Schedule</p>
            <div className="flex flex-wrap gap-1.5">
              {WEEKDAYS.map((day) => {
                const active = weekendDays.includes(day);
                return (
                  <button
                    key={day}
                    type="button"
                    aria-pressed={active}
                    disabled={!canManage}
                    onClick={() => toggleWeekendDay(day)}
                    className={`rounded-small border px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                      active
                        ? "border-[#2A5BDB] bg-[#2A5BDB] text-white"
                        : "border-appBorder bg-[#F8F9FD] text-[#102C54] hover:border-[#2A5BDB]"
                    } disabled:opacity-60`}
                  >
                    {day}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <label htmlFor={probationId} className="block font-semibold text-[#102C54] mb-1">
                Probation (Months)
              </label>
              <input
                id={probationId}
                type="number"
                min={0}
                max={24}
                value={probationMonths}
                onChange={(e) => setProbationMonths(Number(e.target.value) || 0)}
                disabled={!canManage}
                className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
              />
            </div>

            <div>
              <label htmlFor={maxAbsentId} className="block font-semibold text-[#102C54] mb-1">
                Max Team Absent (%)
              </label>
              <input
                id={maxAbsentId}
                type="number"
                min={0}
                max={100}
                step="0.5"
                value={maxTeamAbsentPct}
                onChange={(e) => setMaxTeamAbsentPct(e.target.value)}
                disabled={!canManage}
                className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
              />
            </div>

            <div>
              <label htmlFor={minPresentId} className="block font-semibold text-[#102C54] mb-1">
                Min Team Present
              </label>
              <input
                id={minPresentId}
                type="number"
                min={0}
                max={1000}
                value={minTeamPresent}
                onChange={(e) => setMinTeamPresent(Number(e.target.value) || 0)}
                disabled={!canManage}
                className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
              />
            </div>

            <div>
              <label htmlFor={backupApproverId} className="block font-semibold text-[#102C54] mb-1">
                Backup Approver
              </label>
              <select
                id={backupApproverId}
                value={backupApproverUserId}
                onChange={(e) => setBackupApproverUserId(e.target.value)}
                disabled={!canManage}
                className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
              >
                <option value="">Auto (HR Head / Owner)</option>
                {backupCandidates.map((emp) => (
                  <option key={emp.id} value={emp.user_id || ""}>
                    {emp.full_name} ({emp.job_title})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-small border border-appBorder bg-[#F8F9FD] px-3 py-2.5">
            <div>
              <span className="font-semibold text-[#102C54] block">
                Employee Self Check-In Enabled
              </span>
              <span className="text-[11px] text-textSecondary">
                Allows employees to mark daily `PRESENT` attendance via `/attendance` check-in
                button.
              </span>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={checkInEnabled}
              aria-label="Toggle Employee Self Check-In"
              disabled={!canManage}
              onClick={() => setCheckInEnabled((prev) => !prev)}
              className={`rounded-full px-3 py-1 text-[11px] font-bold transition-colors ${
                checkInEnabled ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-700"
              } disabled:opacity-60`}
            >
              {checkInEnabled ? "Enabled" : "Disabled"}
            </button>
          </div>

          {canManage && (
            <Button
              type="button"
              size="sm"
              disabled={updateWorkWeek.isPending || isOffline}
              onClick={() => void handleSaveWorkWeek()}
              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1.5"
            >
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
              {updateWorkWeek.isPending ? "Saving Rules..." : "Save Work-Week Rules"}
            </Button>
          )}
        </CardContent>
      </Card>

      {/* 2. Leave Types Configuration */}
      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="text-base text-[#102C54]">Leave Types &amp; Allowances</CardTitle>
          <CardDescription className="text-xs text-textSecondary">
            Configure annual quotas, upfront/monthly accrual mode, carry-forward caps, notice days,
            probation rules, and half-day support.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-xs">
          <div className="overflow-x-auto rounded-medium border border-appBorder">
            <table className="w-full min-w-[640px] border-collapse text-left text-xs">
              <thead className="bg-[#F8F9FD] text-[11px] uppercase text-textSecondary">
                <tr>
                  <th scope="col" className="border-b border-appBorder px-3 py-2">
                    Leave Type
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2">
                    Allowance
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2">
                    Accrual
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2">
                    Carry Fwd Cap
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2">
                    Rules
                  </th>
                  {canManage && (
                    <th scope="col" className="border-b border-appBorder px-3 py-2 text-right">
                      Actions
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-appBorder bg-white">
                {leaveTypes.map((lt) => (
                  <tr key={lt.id} className="hover:bg-[#F8F9FD]/60">
                    <th scope="row" className="px-3 py-2.5 font-normal text-left">
                      <span className="font-bold text-[#102C54] block">{lt.name}</span>
                      <span className="font-mono text-[10px] text-textSecondary">{lt.code}</span>
                    </th>
                    <td className="px-3 py-2.5 whitespace-nowrap font-semibold text-[#2A5BDB]">
                      {formatDays(lt.annual_allowance_days)} days
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap capitalize">{lt.accrual_mode}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      {formatDays(lt.carry_forward_cap_days)} days
                    </td>
                    <td className="px-3 py-2.5 text-[11px] text-textSecondary">
                      Notice: {lt.min_notice_days}d &bull;{" "}
                      {lt.allow_during_probation ? "Probation OK" : "No Probation"} &bull;{" "}
                      {lt.allow_half_day ? "Half-Day OK" : "Full-Day Only"}
                      {lt.requires_document_after_days
                        ? ` • Doc > ${lt.requires_document_after_days}d`
                        : ""}
                    </td>
                    {canManage && (
                      <td className="px-3 py-2.5 text-right whitespace-nowrap space-x-1.5">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={updateLeaveType.isPending || isOffline}
                          onClick={() =>
                            void handleQuickUpdateAllowance(
                              lt,
                              toNumericDays(lt.annual_allowance_days) + 1
                            )
                          }
                          aria-label={`Increase allowance for ${lt.name}`}
                          className="text-[11px] px-2 py-1 h-auto"
                        >
                          +1d
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={deleteLeaveType.isPending || isOffline}
                          onClick={() => void handleDeleteLeaveType(lt)}
                          aria-label={`Delete leave type ${lt.name}`}
                          className="text-rose-700 border-rose-200 hover:bg-rose-50 text-[11px] px-2 py-1 h-auto"
                        >
                          <Trash2 className="h-3 w-3" aria-hidden="true" />
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {canManage && (
            <form
              onSubmit={handleAddLeaveType}
              className="grid grid-cols-1 gap-2 sm:grid-cols-6 items-end rounded-small border border-appBorder bg-[#F8F9FD] p-3"
            >
              <div>
                <label htmlFor={newLtCodeId} className="block text-[11px] font-semibold mb-1">
                  Code
                </label>
                <input
                  id={newLtCodeId}
                  type="text"
                  value={newLtCode}
                  onChange={(e) => setNewLtCode(e.target.value)}
                  placeholder="bereavement"
                  className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                />
              </div>
              <div className="sm:col-span-2">
                <label htmlFor={newLtNameId} className="block text-[11px] font-semibold mb-1">
                  Display Name
                </label>
                <input
                  id={newLtNameId}
                  type="text"
                  value={newLtName}
                  onChange={(e) => setNewLtName(e.target.value)}
                  placeholder="Bereavement Leave"
                  className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                />
              </div>
              <div>
                <label htmlFor={newLtDaysId} className="block text-[11px] font-semibold mb-1">
                  Annual Days
                </label>
                <input
                  id={newLtDaysId}
                  type="number"
                  min={0}
                  max={365}
                  value={newLtDays}
                  onChange={(e) => setNewLtDays(e.target.value)}
                  className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                />
              </div>
              <div>
                <label className="block text-[11px] font-semibold mb-1">
                  Accrual / Carry
                  <div className="flex gap-1 mt-1">
                    <select
                      aria-label="Accrual mode"
                      value={newLtAccrual}
                      onChange={(e) => setNewLtAccrual(e.target.value as "upfront" | "monthly")}
                      className="w-full rounded-small border border-slate-300 bg-white px-1.5 py-1.5 text-[11px]"
                    >
                      <option value="upfront">Upfront</option>
                      <option value="monthly">Monthly</option>
                    </select>
                    <input
                      aria-label="Carry-forward cap days"
                      type="number"
                      min={0}
                      max={365}
                      value={newLtCarry}
                      onChange={(e) => setNewLtCarry(e.target.value)}
                      className="w-16 rounded-small border border-slate-300 bg-white px-1.5 py-1.5 text-[11px]"
                    />
                  </div>
                </label>
              </div>
              <Button
                type="submit"
                size="sm"
                disabled={createLeaveType.isPending || isOffline}
                className="bg-[#102C54] text-white hover:bg-[#2A5BDB] inline-flex items-center gap-1"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Add Type
              </Button>
            </form>
          )}
        </CardContent>
      </Card>

      {/* 3. Public Holidays Table */}
      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle className="text-base text-[#102C54]">
                Public Holidays Calendar ({holidays.length})
              </CardTitle>
              <CardDescription className="text-xs text-textSecondary">
                Pakistan gazetted holidays are seeded automatically; customize dates per tenant.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <label htmlFor={holidayYearId} className="font-semibold text-[#102C54]">
                Year:
              </label>
              <input
                id={holidayYearId}
                type="number"
                min={2020}
                max={2050}
                value={holidayYear}
                onChange={(e) => setHolidayYear(Number(e.target.value) || 2026)}
                className="w-20 rounded-small border border-appBorder bg-[#F8F9FD] px-2 py-1 text-xs text-[#102C54]"
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 text-xs">
          {canManage && (
            <form
              onSubmit={handleAddHoliday}
              className="grid grid-cols-1 gap-2 sm:grid-cols-5 items-end rounded-small border border-appBorder bg-[#F8F9FD] p-3"
            >
              <div>
                <label htmlFor={newHolidayDateId} className="block text-[11px] font-semibold mb-1">
                  Holiday Date
                </label>
                <input
                  id={newHolidayDateId}
                  type="date"
                  value={newHolidayDate}
                  onChange={(e) => setNewHolidayDate(e.target.value)}
                  className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                />
              </div>
              <div className="sm:col-span-2">
                <label htmlFor={newHolidayNameId} className="block text-[11px] font-semibold mb-1">
                  Holiday Name
                </label>
                <input
                  id={newHolidayNameId}
                  type="text"
                  value={newHolidayName}
                  onChange={(e) => setNewHolidayName(e.target.value)}
                  placeholder="e.g. Company Foundation Day"
                  className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                />
              </div>
              <label className="inline-flex items-center gap-1.5 pb-1.5 text-[11px] font-semibold cursor-pointer">
                <input
                  type="checkbox"
                  checked={newHolidayOptional}
                  onChange={(e) => setNewHolidayOptional(e.target.checked)}
                  className="accent-[#2A5BDB]"
                />
                Optional Holiday
              </label>
              <Button
                type="submit"
                size="sm"
                disabled={createHoliday.isPending || isOffline}
                className="bg-[#102C54] text-white hover:bg-[#2A5BDB] inline-flex items-center gap-1"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Add Holiday
              </Button>
            </form>
          )}

          <div className="overflow-x-auto rounded-medium border border-appBorder">
            <table className="w-full border-collapse text-left text-xs">
              <thead className="bg-[#F8F9FD] text-[11px] uppercase text-textSecondary">
                <tr>
                  <th scope="col" className="border-b border-appBorder px-3 py-2">
                    Date
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2">
                    Holiday
                  </th>
                  <th scope="col" className="border-b border-appBorder px-3 py-2">
                    Type
                  </th>
                  {canManage && (
                    <th scope="col" className="border-b border-appBorder px-3 py-2 text-right">
                      Actions
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-appBorder bg-white">
                {holidays.length === 0 ? (
                  <tr>
                    <td
                      colSpan={canManage ? 4 : 3}
                      className="px-3 py-4 text-center text-textSecondary"
                    >
                      No holidays found for {holidayYear}.
                    </td>
                  </tr>
                ) : (
                  holidays.map((hol) => (
                    <tr key={hol.id} className="hover:bg-[#F8F9FD]/60">
                      <td className="px-3 py-2 font-mono whitespace-nowrap text-[#102C54]">
                        {hol.holiday_date}
                      </td>
                      <th scope="row" className="px-3 py-2 font-semibold text-[#102C54] text-left">
                        {editingHolidayId === hol.id ? (
                          <div className="flex items-center gap-1.5">
                            <input
                              type="text"
                              aria-label={`Edit holiday name for ${hol.holiday_date}`}
                              value={editHolidayName}
                              onChange={(e) => setEditHolidayName(e.target.value)}
                              className="rounded-small border border-slate-300 px-2 py-1 text-xs"
                            />
                            <Button
                              type="button"
                              size="sm"
                              onClick={() => void handleSaveHolidayEdit(hol.id)}
                              className="bg-[#2A5BDB] text-white text-[11px] px-2 py-1 h-auto"
                            >
                              Save
                            </Button>
                          </div>
                        ) : (
                          hol.name
                        )}
                      </th>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <Badge
                          variant={hol.is_optional ? "outline" : "primary"}
                          className="text-[10px]"
                        >
                          {hol.is_optional ? "Optional" : "Gazetted"}
                        </Badge>
                      </td>
                      {canManage && (
                        <td className="px-3 py-2 text-right whitespace-nowrap space-x-1.5">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setEditingHolidayId(hol.id);
                              setEditHolidayName(hol.name);
                            }}
                            className="text-[11px] px-2 py-1 h-auto"
                          >
                            Edit
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={deleteHoliday.isPending || isOffline}
                            onClick={() => void handleDeleteHoliday(hol.id, hol.name)}
                            aria-label={`Delete holiday ${hol.name}`}
                            className="text-rose-700 border-rose-200 hover:bg-rose-50 text-[11px] px-2 py-1 h-auto"
                          >
                            <Trash2 className="h-3 w-3" aria-hidden="true" />
                          </Button>
                        </td>
                      )}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
