"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, CalendarDays, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { HrOverviewTab } from "@/components/leave/hr-overview-tab";
import { LeaveBalanceCards } from "@/components/leave/leave-balance-cards";
import { LeaveRequestTab } from "@/components/leave/leave-request-tab";
import { ManagerInboxTab } from "@/components/leave/manager-inbox-tab";
import { MyRequestsTab } from "@/components/leave/my-requests-tab";
import { canApproveLeave, canManageLeave } from "@/components/leave/leave-status-badges";
import { TeamCalendarTab } from "@/components/leave/team-calendar-tab";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useHydrated } from "@/hooks/use-hydrated";
import {
  useLeaveInbox,
  useLeaveTypes,
  useMyLeaveBalances,
  useMyLeaveRequests,
  useWorkWeek,
} from "@/hooks/use-leave";
import { isOfflineError } from "@/lib/api-request";

type LeaveTabId = "request" | "my-requests" | "inbox" | "calendar" | "overview";

export default function LeaveWorkspacePage() {
  const tabReqId = useId();
  const tabMyId = useId();
  const tabInboxId = useId();
  const tabCalId = useId();
  const tabOverviewId = useId();
  const tabRefs = useRef<Partial<Record<LeaveTabId, HTMLButtonElement | null>>>({});

  // SSR/hydration guard & deterministic year synced after mount
  const isHydrated = useHydrated();
  const [currentYear, setCurrentYear] = useState(2026);
  useEffect(() => {
    setCurrentYear(new Date().getFullYear());
  }, []);

  const [activeTab, setActiveTab] = useState<LeaveTabId>("request");

  const { data: currentUser } = useCurrentUser();
  const canApprove = isHydrated && canApproveLeave(currentUser);
  const canManage = isHydrated && canManageLeave(currentUser);

  const {
    isStandby: isBackendStandby,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();

  const {
    data: balances = [],
    isLoading: isBalancesLoading,
    error: balancesError,
    refetch: refetchBalances,
  } = useMyLeaveBalances(currentYear);

  const { data: leaveTypes = [], error: typesError } = useLeaveTypes(false);
  const { data: workWeek } = useWorkWeek();

  const {
    data: myRequests = [],
    isLoading: isMyRequestsLoading,
    error: myRequestsError,
    refetch: refetchMyRequests,
  } = useMyLeaveRequests();

  // Strictly role-gate inbox query so Employees never fire background 403s
  const {
    data: inboxRequests = [],
    isLoading: isInboxLoading,
    error: inboxError,
    refetch: refetchInbox,
  } = useLeaveInbox(canApprove);

  const isOffline =
    isHydrated &&
    (isBackendStandby ||
      isOfflineError(balancesError) ||
      isOfflineError(typesError) ||
      isOfflineError(myRequestsError) ||
      isOfflineError(inboxError));

  const liveHttpError =
    isHydrated && balancesError && !isOfflineError(balancesError)
      ? balancesError.message
      : isHydrated && myRequestsError && !isOfflineError(myRequestsError)
        ? myRequestsError.message
        : null;

  const visibleTabs: Array<{ id: LeaveTabId; domId: string; label: string; count?: number }> = [
    { id: "request", domId: tabReqId, label: "Request Leave" },
    {
      id: "my-requests",
      domId: tabMyId,
      label: "My Requests",
      count: isHydrated ? myRequests.length : 0,
    },
    ...(canApprove
      ? [
          {
            id: "inbox" as const,
            domId: tabInboxId,
            label: "Manager Inbox",
            count: inboxRequests.length,
          },
        ]
      : []),
    { id: "calendar", domId: tabCalId, label: "Team Calendar" },
    ...(canManage ? [{ id: "overview" as const, domId: tabOverviewId, label: "HR Overview" }] : []),
  ];

  const resolvedTab: LeaveTabId = visibleTabs.some((t) => t.id === activeTab)
    ? activeTab
    : "request";
  const activeTabMeta = visibleTabs.find((t) => t.id === resolvedTab) ?? visibleTabs[0];

  const handleTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, idx: number) => {
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
            <span className="text-xs text-[#102C54] font-semibold">Leave Management</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[#102C54] flex items-center gap-2">
            <CalendarDays className="h-6 w-6 text-[#2A5BDB]" aria-hidden="true" />
            Leave Management &amp; AI Intake
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Double-entry leave balances, bilingual AI intake, policy checks, and team approvals.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="primary">Stage 6 Deterministic Policy</Badge>
          <Badge variant="outline">
            PKT ({(isHydrated && workWeek?.timezone) || "Asia/Karachi"})
          </Badge>
        </div>
      </div>

      {isOffline && (
        <StandbyBanner
          message="FastAPI backend is offline. Leave submissions, approvals, and live balance queries require an active backend connection."
          onRetry={() => {
            refetchHealth();
            refetchBalances();
            refetchMyRequests();
            if (canApprove) refetchInbox();
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

      {/* Pinned Balance Cards */}
      <LeaveBalanceCards
        balances={isHydrated ? balances : []}
        isLoading={isHydrated ? isBalancesLoading : false}
        year={currentYear}
      />

      {/* WAI-ARIA Tablist */}
      <div className="border-b border-appBorder bg-white rounded-t-medium px-2">
        <div
          role="tablist"
          aria-label="Leave workspace sections"
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
                {typeof tab.count === "number" && tab.count > 0 && (
                  <span className="inline-flex items-center justify-center rounded-full bg-[#2A5BDB]/10 px-2 py-0.5 text-[10px] font-bold text-[#102C54]">
                    {tab.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Active Tab Panel */}
      <div role="tabpanel" aria-labelledby={activeTabMeta.domId}>
        {resolvedTab === "request" && (
          <LeaveRequestTab
            leaveTypes={isHydrated ? leaveTypes : []}
            isOffline={isOffline}
            onSubmitted={() => setActiveTab("my-requests")}
          />
        )}

        {resolvedTab === "my-requests" && (
          <MyRequestsTab
            requests={isHydrated ? myRequests : []}
            isLoading={isHydrated ? isMyRequestsLoading : false}
            isOffline={isOffline}
          />
        )}

        {resolvedTab === "inbox" && canApprove && (
          <ManagerInboxTab
            requests={isHydrated ? inboxRequests : []}
            isLoading={isHydrated ? isInboxLoading : false}
            isOffline={isOffline}
          />
        )}

        {resolvedTab === "calendar" && (
          <TeamCalendarTab workWeek={isHydrated ? workWeek : undefined} isOffline={isOffline} />
        )}

        {resolvedTab === "overview" && canManage && (
          <HrOverviewTab canManage={canManage} isOffline={isOffline} />
        )}
      </div>
    </div>
  );
}
