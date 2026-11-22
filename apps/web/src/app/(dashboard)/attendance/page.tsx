"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, CalendarCheck2, Info, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { AttendanceImportTab } from "@/components/attendance/attendance-import-tab";
import {
  canAccessDailyGrid,
  canManageAttendance,
} from "@/components/attendance/attendance-status-badges";
import { DailyGridTab } from "@/components/attendance/daily-grid-tab";
import { MonthReviewTab } from "@/components/attendance/month-review-tab";
import { SelfCheckInCard } from "@/components/attendance/self-check-in-card";
import { useMyTodayAttendance } from "@/hooks/use-attendance";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { isOfflineError } from "@/lib/api-request";

type AttendanceTabId = "daily-grid" | "excel-import" | "month-review";

export default function AttendanceWorkspacePage() {
  const tabGridId = useId();
  const tabImportId = useId();
  const tabReviewId = useId();
  const tabRefs = useRef<Partial<Record<AttendanceTabId, HTMLButtonElement | null>>>({});

  const [isHydrated, setIsHydrated] = useState(false);
  useEffect(() => {
    setIsHydrated(true);
  }, []);

  const [activeTab, setActiveTab] = useState<AttendanceTabId>("daily-grid");

  const { data: currentUser } = useCurrentUser();
  const canGrid = isHydrated && canAccessDailyGrid(currentUser);
  const canManage = isHydrated && canManageAttendance(currentUser);

  const {
    isStandby: isBackendStandby,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();

  const { data: todayData, error: todayError, refetch: refetchToday } = useMyTodayAttendance();

  const isOffline = isHydrated && (isBackendStandby || isOfflineError(todayError));
  const liveHttpError =
    isHydrated && todayError && !isOfflineError(todayError) ? todayError.message : null;

  const visibleTabs: Array<{ id: AttendanceTabId; domId: string; label: string }> = [
    ...(canGrid
      ? [{ id: "daily-grid" as const, domId: tabGridId, label: "Manager Daily Grid" }]
      : []),
    ...(canManage
      ? [{ id: "excel-import" as const, domId: tabImportId, label: "Excel Import" }]
      : []),
    ...(canManage
      ? [{ id: "month-review" as const, domId: tabReviewId, label: "Month Review & Lock" }]
      : []),
  ];

  const resolvedTab: AttendanceTabId | null =
    visibleTabs.find((t) => t.id === activeTab)?.id ?? visibleTabs[0]?.id ?? null;
  const activeTabMeta = visibleTabs.find((t) => t.id === resolvedTab);

  const handleTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, idx: number) => {
    if (visibleTabs.length === 0) return;
    let nextIdx: number | null = null;
    if (e.key === "ArrowRight") nextIdx = (idx + 1) % visibleTabs.length;
    else if (e.key === "ArrowLeft") nextIdx = (idx - 1 + visibleTabs.length) % visibleTabs.length;
    else if (e.key === "Home") nextIdx = 0;
    else if (e.key === "End") nextIdx = visibleTabs.length - 1;

    if (nextIdx !== null) {
      e.preventDefault();
      const target = visibleTabs[nextIdx];
      if (target) {
        setActiveTab(target.id);
        tabRefs.current[target.id]?.focus();
      }
    }
  };

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-appBorder pb-5">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              href="/"
              className="text-xs text-textSecondary hover:text-[#2A5BDB] flex items-center gap-1 font-medium transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              Dashboard
            </Link>
            <span className="text-textMuted text-xs">/</span>
            <span className="text-xs text-[#102C54] font-semibold">Attendance</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[#102C54] flex items-center gap-2">
            <CalendarCheck2 className="h-6 w-6 text-[#2A5BDB]" aria-hidden="true" />
            Attendance &amp; Payroll Month Lock
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Daily self check-in, manager grid, two-phase Excel import, gap verification &amp;
            governed month locks.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="primary">Stage 6 Month Governance</Badge>
          <Badge variant="outline">PKT (Asia/Karachi)</Badge>
        </div>
      </div>

      {isOffline && (
        <StandbyBanner
          message="FastAPI backend is offline. Attendance check-in, daily grids, imports, and month locking require an active backend connection."
          onRetry={() => {
            refetchHealth();
            refetchToday();
          }}
          isRetrying={isHealthFetching}
        />
      )}

      {liveHttpError && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
          <span className="font-medium">{liveHttpError}</span>
        </div>
      )}

      {/* Top Banner: Self Check-In Card (shown ONLY when check_in_enabled === true) */}
      {isHydrated && todayData && todayData.check_in_enabled && (
        <SelfCheckInCard todayData={todayData} isOffline={isOffline} />
      )}

      {/* Refinement 3: Plain Employee Read-Only Empty State when check-in is disabled */}
      {isHydrated && !canGrid && todayData && !todayData.check_in_enabled && (
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardContent className="flex items-start gap-3 py-5 text-xs text-textSecondary">
            <Info className="h-4 w-4 text-[#2A5BDB] shrink-0 mt-0.5" aria-hidden="true" />
            <div className="space-y-1">
              <p className="font-bold text-[#102C54]">
                Self Check-In is Disabled for This Organization
              </p>
              <p>
                Daily attendance is recorded and maintained by your Manager or HR department. Check
                your leave balances or submit time-off requests in{" "}
                <Link href="/leave" className="font-semibold text-[#2A5BDB] hover:underline">
                  Leave Management
                </Link>
                .
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Role-Gated Manager / HR Workspace Tabs */}
      {visibleTabs.length > 0 && activeTabMeta && (
        <>
          <div className="border-b border-appBorder bg-white rounded-t-medium px-2">
            <div
              role="tablist"
              aria-label="Attendance workspace sections"
              className="flex overflow-x-auto gap-1"
            >
              {visibleTabs.map((tab, idx) => {
                const selected = resolvedTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    ref={(el) => {
                      tabRefs.current[tab.id] = el;
                    }}
                    id={tab.domId}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    tabIndex={selected ? 0 : -1}
                    onClick={() => setActiveTab(tab.id)}
                    onKeyDown={(e) => handleTabKeyDown(e, idx)}
                    className={`relative shrink-0 whitespace-nowrap inline-flex items-center gap-2 px-4 py-3 text-xs transition-colors border-b-[3px] focus-visible:outline-2 focus-visible:outline-[#2A5BDB] ${
                      selected
                        ? "border-[#2A5BDB] text-[#102C54] font-bold"
                        : "border-transparent text-textSecondary font-medium hover:text-[#102C54]"
                    }`}
                  >
                    <span>{tab.label}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div role="tabpanel" aria-labelledby={activeTabMeta.domId}>
            {resolvedTab === "daily-grid" && canGrid && (
              <DailyGridTab canAccess={canGrid} canManage={canManage} isOffline={isOffline} />
            )}
            {resolvedTab === "excel-import" && canManage && (
              <AttendanceImportTab canManage={canManage} isOffline={isOffline} />
            )}
            {resolvedTab === "month-review" && canManage && (
              <MonthReviewTab canManage={canManage} isOffline={isOffline} />
            )}
          </div>
        </>
      )}
    </div>
  );
}
