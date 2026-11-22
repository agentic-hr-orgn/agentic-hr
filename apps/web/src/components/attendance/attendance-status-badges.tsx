"use client";

import React from "react";
import { Badge } from "@/components/ui/badge";
import type { AuthenticatedUserContext } from "@/hooks/use-current-user";

export type AttendanceStatusOption =
  "PRESENT" | "ABSENT" | "HALF_DAY" | "ON_LEAVE" | "WFH" | "HOLIDAY" | "WEEKEND";

export const ATTENDANCE_STATUSES: AttendanceStatusOption[] = [
  "PRESENT",
  "WFH",
  "HALF_DAY",
  "ABSENT",
  "ON_LEAVE",
  "HOLIDAY",
  "WEEKEND",
];

export const ATTENDANCE_STATUS_LABELS: Record<AttendanceStatusOption, string> = {
  PRESENT: "Present",
  WFH: "Work From Home",
  HALF_DAY: "Half Day",
  ABSENT: "Absent",
  ON_LEAVE: "On Leave",
  HOLIDAY: "Holiday",
  WEEKEND: "Weekend",
};

export function canAccessDailyGrid(user: AuthenticatedUserContext | null | undefined): boolean {
  if (!user) return false;
  const role = (user.role || "").toLowerCase();
  return role === "owner" || role === "manager" || user.is_hr_head === true;
}

export function canManageAttendance(user: AuthenticatedUserContext | null | undefined): boolean {
  if (!user) return false;
  const role = (user.role || "").toLowerCase();
  return role === "owner" || user.is_hr_head === true;
}

export function AttendanceStatusBadge({ status }: { status: string | null | undefined }) {
  const norm = (status || "").toUpperCase();
  if (norm === "PRESENT") {
    return (
      <Badge variant="success" className="text-[11px]">
        Present
      </Badge>
    );
  }
  if (norm === "WFH") {
    return (
      <Badge variant="primary" className="text-[11px]">
        WFH
      </Badge>
    );
  }
  if (norm === "HALF_DAY") {
    return (
      <Badge variant="warning" className="text-[11px]">
        Half Day
      </Badge>
    );
  }
  if (norm === "ABSENT") {
    return (
      <Badge variant="outline" className="text-[11px] bg-rose-50 text-rose-700 border-rose-200">
        Absent
      </Badge>
    );
  }
  if (norm === "ON_LEAVE") {
    return (
      <Badge
        variant="outline"
        className="text-[11px] bg-indigo-50 text-indigo-800 border-indigo-200"
      >
        On Leave
      </Badge>
    );
  }
  if (norm === "HOLIDAY") {
    return (
      <Badge variant="primary" className="text-[11px]">
        Holiday
      </Badge>
    );
  }
  if (norm === "WEEKEND") {
    return (
      <Badge variant="outline" className="text-[11px] bg-slate-100 text-slate-600 border-slate-200">
        Weekend
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-[11px] text-textMuted border-appBorder">
      Unmarked
    </Badge>
  );
}
