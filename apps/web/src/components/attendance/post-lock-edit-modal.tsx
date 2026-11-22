"use client";

import React, { useEffect, useId, useState } from "react";
import { Lock, Send, X, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  ATTENDANCE_STATUSES,
  ATTENDANCE_STATUS_LABELS,
  type AttendanceStatusOption,
} from "@/components/attendance/attendance-status-badges";
import { useRequestPostLockEdit } from "@/hooks/use-attendance";
import { useToastStore } from "@/stores/toast-store";

export interface PostLockEmployeeOption {
  employee_id: string;
  employee_name: string;
  employee_email: string;
}

interface PostLockEditModalProps {
  yearMonth: string;
  monthStart: string;
  monthEnd: string;
  employees: PostLockEmployeeOption[];
  defaultEmployeeId?: string;
  defaultDate?: string;
  isOffline: boolean;
  onClose: () => void;
}

export function PostLockEditModal({
  yearMonth,
  monthStart,
  monthEnd,
  employees,
  defaultEmployeeId,
  defaultDate,
  isOffline,
  onClose,
}: PostLockEditModalProps) {
  const titleId = useId();
  const empSelectId = useId();
  const dateInputId = useId();
  const statusSelectId = useId();
  const approverRoleId = useId();
  const notesInputId = useId();
  const reasonInputId = useId();

  const requestEdit = useRequestPostLockEdit();
  const pushToast = useToastStore((s) => s.pushToast);

  const [employeeId, setEmployeeId] = useState(
    defaultEmployeeId || employees[0]?.employee_id || ""
  );
  const [attendanceDate, setAttendanceDate] = useState(defaultDate || monthStart);
  const [newStatus, setNewStatus] = useState<AttendanceStatusOption>("PRESENT");
  const [approverRole, setApproverRole] = useState<"hr_head" | "owner">("hr_head");
  const [notes, setNotes] = useState("");
  const [reason, setReason] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (requestEdit.isPending || isOffline) return;
    setErrorMessage(null);

    if (!employeeId || !attendanceDate || reason.trim().length < 3) {
      setErrorMessage(
        "Employee, attendance date, and justification reason (3+ chars) are required."
      );
      return;
    }

    try {
      const res = await requestEdit.mutateAsync({
        employee_id: employeeId,
        attendance_date: attendanceDate,
        new_status: newStatus,
        approver_role: approverRole,
        notes: notes.trim() || undefined,
        reason: reason.trim(),
      });
      pushToast({
        variant: "success",
        title: "Post-Lock Edit Approval Requested",
        description: `Correction for ${res.employee_name} on ${res.attendance_date} (${res.new_status}) awaits ${res.approver_role} approval.`,
        actionLabel: "Open Approvals",
        actionHref: "/approvals",
      });
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to submit post-lock edit request.";
      setErrorMessage(msg);
      pushToast({ variant: "error", title: "Post-Lock Edit Failed", description: msg });
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#102C54]/50 p-4 backdrop-blur-[1px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto overscroll-contain rounded-medium border border-appBorder bg-white shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-appBorder bg-[#F8F9FD] px-5 py-4">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-small bg-amber-100 text-amber-800">
              <Lock className="h-4 w-4" aria-hidden="true" />
            </div>
            <div>
              <h2 id={titleId} className="text-base font-bold text-[#102C54]">
                Request Post-Lock Edit ({yearMonth})
              </h2>
              <p className="text-[11px] text-textSecondary">
                Creates a Stage 2 governance approval (`attendance.post_lock_edit`) with audit diff.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close post-lock edit modal"
            className="rounded-small p-1.5 text-textSecondary hover:bg-slate-200/70 hover:text-[#102C54]"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4 p-5 text-xs">
          {errorMessage && (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-small border border-rose-200 bg-rose-50 px-3 py-2 text-rose-900"
            >
              <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
              <span className="font-medium">{errorMessage}</span>
            </div>
          )}

          <div>
            <label htmlFor={empSelectId} className="block font-semibold text-[#102C54] mb-1">
              Employee
            </label>
            <select
              id={empSelectId}
              value={employeeId}
              onChange={(e) => setEmployeeId(e.target.value)}
              required
              className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
            >
              {employees.map((emp) => (
                <option key={emp.employee_id} value={emp.employee_id}>
                  {emp.employee_name} ({emp.employee_email})
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={dateInputId} className="block font-semibold text-[#102C54] mb-1">
                Attendance Date
              </label>
              <input
                id={dateInputId}
                type="date"
                min={monthStart}
                max={monthEnd}
                value={attendanceDate}
                onChange={(e) => setAttendanceDate(e.target.value)}
                required
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
              />
            </div>

            <div>
              <label htmlFor={statusSelectId} className="block font-semibold text-[#102C54] mb-1">
                Corrected Status
              </label>
              <select
                id={statusSelectId}
                value={newStatus}
                onChange={(e) => setNewStatus(e.target.value as AttendanceStatusOption)}
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
              >
                {ATTENDANCE_STATUSES.map((st) => (
                  <option key={st} value={st}>
                    {ATTENDANCE_STATUS_LABELS[st]} ({st})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={approverRoleId} className="block font-semibold text-[#102C54] mb-1">
                Approver Gate
              </label>
              <select
                id={approverRoleId}
                value={approverRole}
                onChange={(e) => setApproverRole(e.target.value as "hr_head" | "owner")}
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
              >
                <option value="hr_head">HR Head Approval</option>
                <option value="owner">Owner Approval</option>
              </select>
            </div>

            <div>
              <label htmlFor={notesInputId} className="block font-semibold text-[#102C54] mb-1">
                Updated Notes (Optional)
              </label>
              <input
                id={notesInputId}
                type="text"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="e.g. Retroactive correction"
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
              />
            </div>
          </div>

          <div>
            <label htmlFor={reasonInputId} className="block font-semibold text-[#102C54] mb-1">
              Justification Reason
            </label>
            <textarea
              id={reasonInputId}
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              minLength={3}
              placeholder="Explain why a payroll-locked date requires modification..."
              className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
            />
          </div>

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-appBorder">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={requestEdit.isPending || isOffline}
              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1.5"
            >
              <Send className="h-3.5 w-3.5" aria-hidden="true" />
              {requestEdit.isPending ? "Submitting..." : "Submit Post-Lock Request"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
