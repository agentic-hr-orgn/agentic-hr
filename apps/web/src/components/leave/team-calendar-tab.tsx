"use client";

import React, { useEffect, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import type {
  HolidayResponse,
  TeamCalendarItemResponse,
  WorkWeekResponse,
} from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useHolidays, useTeamLeaveCalendar } from "@/hooks/use-leave";

interface TeamCalendarTabProps {
  workWeek?: WorkWeekResponse;
  isOffline: boolean;
}

const WEEKDAY_HEADERS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAY_NAMES_BY_INDEX = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

function getMonthBounds(yearMonth: string): {
  year: number;
  month: number;
  startDate: string;
  endDate: string;
  daysInMonth: number;
  startWeekday: number;
  label: string;
} {
  const [yStr, mStr] = yearMonth.split("-");
  const year = Number(yStr) || 2026;
  const month = Number(mStr) || 11;
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const startWeekday = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  const mm = String(month).padStart(2, "0");
  const label = new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  return {
    year,
    month,
    startDate: `${year}-${mm}-01`,
    endDate: `${year}-${mm}-${String(daysInMonth).padStart(2, "0")}`,
    daysInMonth,
    startWeekday,
    label,
  };
}

function shiftMonth(yearMonth: string, delta: number): string {
  const [yStr, mStr] = yearMonth.split("-");
  const total = Number(yStr) * 12 + (Number(mStr) - 1) + delta;
  const nextY = Math.floor(total / 12);
  const nextM = (total % 12) + 1;
  return `${nextY}-${String(nextM).padStart(2, "0")}`;
}

export function TeamCalendarTab({ workWeek, isOffline }: TeamCalendarTabProps) {
  // Deterministic SSR default synced to user's current month after mount to avoid hydration #418
  const [viewMonth, setViewMonth] = useState("2026-11");
  useEffect(() => {
    const now = new Date();
    const currentYm = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    setViewMonth(currentYm);
  }, []);

  const bounds = getMonthBounds(viewMonth);
  const { data: calendarItems = [], isLoading } = useTeamLeaveCalendar(
    { startDate: bounds.startDate, endDate: bounds.endDate },
    !isOffline
  );
  const { data: holidays = [] } = useHolidays(bounds.year, !isOffline);

  const weekendSet = new Set(
    (workWeek?.weekend_days ?? ["Saturday", "Sunday"]).map((d) => d.trim().toLowerCase())
  );
  const holidayByDate = new Map<string, HolidayResponse>();
  for (const h of holidays) {
    holidayByDate.set(h.holiday_date, h);
  }

  const cells: Array<{ day: number | null; iso: string | null; weekdayName: string }> = [];
  for (let i = 0; i < bounds.startWeekday; i++) {
    cells.push({ day: null, iso: null, weekdayName: "" });
  }
  const mm = String(bounds.month).padStart(2, "0");
  for (let day = 1; day <= bounds.daysInMonth; day++) {
    const iso = `${bounds.year}-${mm}-${String(day).padStart(2, "0")}`;
    const idx = new Date(Date.UTC(bounds.year, bounds.month - 1, day)).getUTCDay();
    cells.push({ day, iso, weekdayName: WEEKDAY_NAMES_BY_INDEX[idx] || "" });
  }

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <CalendarDays className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              <CardTitle className="text-base text-[#102C54]">
                Team Leave Calendar &mdash; {bounds.label}
              </CardTitle>
            </div>
            <CardDescription className="text-xs text-textSecondary">
              Displays approved &amp; pending team leaves overlaid on tenant weekends and Pakistan
              public holidays.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-label="Previous month"
              onClick={() => setViewMonth((prev) => shiftMonth(prev, -1))}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </Button>
            <span className="min-w-[110px] text-center text-xs font-bold text-[#102C54]">
              {bounds.label}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-label="Next month"
              onClick={() => setViewMonth((prev) => shiftMonth(prev, 1))}
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3 text-[11px] text-textSecondary">
          <Badge variant="success">Approved Leave</Badge>
          <Badge variant="warning">Pending Leave</Badge>
          <Badge variant="primary">Public Holiday</Badge>
          <Badge variant="outline">Weekend</Badge>
        </div>

        {isLoading ? (
          <p className="py-8 text-center text-xs text-textSecondary">
            Loading team leave calendar...
          </p>
        ) : (
          <div className="overflow-x-auto">
            <div className="grid min-w-[640px] grid-cols-7 gap-1.5 text-xs">
              {WEEKDAY_HEADERS.map((wd) => (
                <div
                  key={wd}
                  className="rounded-small bg-[#102C54] px-2 py-1.5 text-center text-[11px] font-bold text-white"
                >
                  {wd}
                </div>
              ))}
              {cells.map((cell, idx) => {
                if (!cell.day || !cell.iso) {
                  return (
                    <div
                      key={`empty-${idx}`}
                      className="min-h-[90px] rounded-small border border-dashed border-slate-200 bg-slate-50/50"
                    />
                  );
                }
                const isWeekend = weekendSet.has(cell.weekdayName.toLowerCase());
                const holiday = holidayByDate.get(cell.iso);
                const dayLeaves: TeamCalendarItemResponse[] = calendarItems.filter(
                  (item) => cell.iso! >= item.start_date && cell.iso! <= item.end_date
                );

                return (
                  <div
                    key={cell.iso}
                    className={`min-h-[90px] rounded-small border p-1.5 space-y-1 ${
                      holiday
                        ? "border-blue-200 bg-blue-50/60"
                        : isWeekend
                          ? "border-slate-200 bg-slate-100/70"
                          : "border-appBorder bg-white"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold text-[#102C54]">{cell.day}</span>
                      {isWeekend && (
                        <span className="text-[9px] font-semibold uppercase text-textMuted">
                          Wknd
                        </span>
                      )}
                    </div>
                    {holiday && (
                      <div className="rounded bg-[#2A5BDB] px-1.5 py-0.5 text-[10px] font-semibold text-white truncate">
                        {holiday.name}
                      </div>
                    )}
                    {dayLeaves.map((lv) => {
                      const isApp = lv.status.toLowerCase() === "approved";
                      return (
                        <div
                          key={`${lv.request_id}-${cell.iso}`}
                          className={`rounded px-1.5 py-0.5 text-[10px] font-medium truncate ${
                            isApp
                              ? "bg-emerald-100 text-emerald-900 border border-emerald-200"
                              : "bg-amber-100 text-amber-950 border border-amber-200"
                          }`}
                          title={`${lv.employee_name} — ${lv.leave_type_name} (${lv.status})`}
                        >
                          {lv.employee_name} ({lv.leave_type_code})
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
