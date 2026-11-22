"use client";

import React from "react";
import { CheckCircle2, Clock, Lock, LogIn, LogOut } from "lucide-react";
import type { AttendanceTodayResponse } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AttendanceStatusBadge } from "@/components/attendance/attendance-status-badges";
import { useCheckIn } from "@/hooks/use-attendance";
import { formatPKT } from "@/lib/format";
import { useToastStore } from "@/stores/toast-store";

interface SelfCheckInCardProps {
  todayData: AttendanceTodayResponse;
  isOffline: boolean;
}

export function SelfCheckInCard({ todayData, isOffline }: SelfCheckInCardProps) {
  const checkInMutation = useCheckIn();
  const pushToast = useToastStore((s) => s.pushToast);

  if (!todayData.check_in_enabled) {
    return null;
  }

  const record = todayData.record;
  const hasCheckedIn = Boolean(record?.check_in_at);
  const hasCheckedOut = Boolean(record?.check_out_at);
  const isLocked = todayData.is_locked || Boolean(record?.locked);

  const handleAction = async (action: "check_in" | "check_out") => {
    if (checkInMutation.isPending || isLocked || isOffline) return;
    try {
      const updated = await checkInMutation.mutateAsync({
        action,
        check_out: action === "check_out",
        target_date: todayData.today_date,
      });
      pushToast({
        variant: "success",
        title: action === "check_out" ? "Checked Out" : "Checked In",
        description: `Recorded ${updated.status} for ${updated.attendance_date} (PKT).`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update check-in status.";
      pushToast({ variant: "error", title: "Check-In Error", description: msg });
    }
  };

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardContent className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 py-4">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Clock className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
            <span className="text-sm font-bold text-[#102C54]">
              Today&apos;s Self Check-In &mdash; {todayData.today_date} (PKT)
            </span>
            <AttendanceStatusBadge status={record?.status} />
            {isLocked && (
              <Badge variant="warning" className="text-[10px] inline-flex items-center gap-1">
                <Lock className="h-3 w-3" aria-hidden="true" />
                Month Locked
              </Badge>
            )}
          </div>
          <p className="text-xs text-textSecondary">
            {hasCheckedIn ? (
              <>
                Checked in at{" "}
                <span className="font-semibold text-[#102C54]">
                  {formatPKT(record?.check_in_at)}
                </span>
                {hasCheckedOut && record?.check_out_at ? (
                  <>
                    {" "}
                    &bull; Checked out at{" "}
                    <span className="font-semibold text-[#102C54]">
                      {formatPKT(record.check_out_at)}
                    </span>
                  </>
                ) : null}
              </>
            ) : (
              "You have not checked in yet today. Record your daily attendance with one click."
            )}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {!hasCheckedIn ? (
            <Button
              type="button"
              disabled={checkInMutation.isPending || isLocked || isOffline}
              onClick={() => void handleAction("check_in")}
              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1.5 text-xs"
            >
              <LogIn className="h-3.5 w-3.5" aria-hidden="true" />
              {checkInMutation.isPending ? "Checking In..." : "Check In"}
            </Button>
          ) : !hasCheckedOut ? (
            <Button
              type="button"
              variant="outline"
              disabled={checkInMutation.isPending || isLocked || isOffline}
              onClick={() => void handleAction("check_out")}
              className="border-[#102C54] text-[#102C54] hover:bg-[#F8F9FD] inline-flex items-center gap-1.5 text-xs"
            >
              <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              {checkInMutation.isPending ? "Checking Out..." : "Check Out"}
            </Button>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-small border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-800">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              Day Completed
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
