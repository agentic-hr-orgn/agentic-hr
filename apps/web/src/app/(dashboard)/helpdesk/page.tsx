"use client";

import React, { Suspense, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { LifeBuoy, PlusCircle, XCircle } from "lucide-react";
import type { TicketStatus } from "@agentic-hr/contracts";
import { AssignmentRulesTab } from "@/components/helpdesk/assignment-rules-tab";
import { HelpdeskAskCard } from "@/components/helpdesk/helpdesk-ask-card";
import { NewTicketModal } from "@/components/helpdesk/new-ticket-modal";
import { StaffQueueTab, type StaffQueueFilters } from "@/components/helpdesk/staff-queue-tab";
import { TicketDetailDrawer } from "@/components/helpdesk/ticket-detail-drawer";
import {
  STATUS_LABELS,
  TICKET_STATUSES,
  TicketCategoryBadge,
  TicketPriorityBadge,
  TicketSlaBadge,
  TicketStatusBadge,
  canAccessStaffQueue,
  canManageTicketRules,
  type StaffAssigneeOption,
} from "@/components/helpdesk/ticket-status-badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useEmployees } from "@/hooks/use-employees";
import { useHydrated } from "@/hooks/use-hydrated";
import { useMyTickets, useStaffTicketQueue, useTicketRules } from "@/hooks/use-tickets";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";

type HelpdeskTabId = "my-tickets" | "staff-queue" | "assignment-rules";

function HelpdeskWorkspaceContent() {
  const searchParams = useSearchParams();
  const deepLinkedTicketId = (searchParams.get("ticket") ?? "").trim() || null;

  const tabMyId = useId();
  const tabQueueId = useId();
  const tabRulesId = useId();
  const tabRefs = useRef<Partial<Record<HelpdeskTabId, HTMLButtonElement | null>>>({});

  const hydrated = useHydrated();
  const { data: currentUser, error: userError, refetch: refetchUser } = useCurrentUser();

  const canHandleTickets = hydrated && canAccessStaffQueue(currentUser);
  const canManageRules = hydrated && canManageTicketRules(currentUser);

  const {
    isStandby: isBackendStandby,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();

  const [activeTab, setActiveTab] = useState<HelpdeskTabId>("my-tickets");
  const [myStatusFilter, setMyStatusFilter] = useState<TicketStatus | "">("");
  const [staffFilters, setStaffFilters] = useState<StaffQueueFilters>({
    status: "",
    category: "",
    priority: "",
    search: "",
  });
  const [manualSelectedTicketId, setManualSelectedTicketId] = useState<string | null>(null);
  const [isNewModalOpen, setIsNewModalOpen] = useState(false);

  const {
    data: myTickets = [],
    isLoading: isMyTicketsLoading,
    error: myTicketsError,
    refetch: refetchMyTickets,
    isFetching: isMyTicketsFetching,
  } = useMyTickets(myStatusFilter ? { status: myStatusFilter } : undefined);

  const queueQueryFilters = useMemo(
    () => ({
      status: staffFilters.status || undefined,
      category: staffFilters.category || undefined,
      priority: staffFilters.priority || undefined,
    }),
    [staffFilters.status, staffFilters.category, staffFilters.priority]
  );

  // Role-gated queries default to disabled for plain Employees -> zero background 403s
  const {
    data: staffQueue = [],
    isLoading: isQueueLoading,
    error: queueError,
    refetch: refetchQueue,
    isFetching: isQueueFetching,
  } = useStaffTicketQueue(queueQueryFilters, { enabled: canHandleTickets });

  const {
    data: ticketRules = [],
    isLoading: isRulesLoading,
    error: rulesError,
    refetch: refetchRules,
    isFetching: isRulesFetching,
  } = useTicketRules({ enabled: canManageRules });

  const { data: employeesData } = useEmployees({ pageSize: 50, enabled: canHandleTickets });

  const staffOptions: StaffAssigneeOption[] = useMemo(() => {
    if (!employeesData?.items) return [];
    return employeesData.items
      .filter((emp): emp is typeof emp & { user_id: string } => Boolean(emp.user_id))
      .map((emp) => ({
        user_id: emp.user_id,
        full_name: emp.full_name,
        email: emp.email,
      }));
  }, [employeesData]);

  const isOffline =
    hydrated &&
    (isBackendStandby ||
      isOfflineError(userError) ||
      isOfflineError(myTicketsError) ||
      isOfflineError(queueError) ||
      isOfflineError(rulesError));

  const liveHttpError = useMemo(() => {
    if (!hydrated) return null;
    if (myTicketsError && !isOfflineError(myTicketsError)) return myTicketsError.message;
    if (queueError && !isOfflineError(queueError)) return queueError.message;
    if (rulesError && !isOfflineError(rulesError)) return rulesError.message;
    if (userError && !isOfflineError(userError)) return userError.message;
    return null;
  }, [hydrated, myTicketsError, queueError, rulesError, userError]);

  const visibleTabs: Array<{ id: HelpdeskTabId; domId: string; label: string; count?: number }> = [
    {
      id: "my-tickets",
      domId: tabMyId,
      label: "My Tickets",
      count: hydrated ? myTickets.length : 0,
    },
    ...(canHandleTickets
      ? [
          {
            id: "staff-queue" as const,
            domId: tabQueueId,
            label: "Staff Queue",
            count: staffQueue.length,
          },
        ]
      : []),
    ...(canManageRules
      ? [
          {
            id: "assignment-rules" as const,
            domId: tabRulesId,
            label: "Assignment Rules",
          },
        ]
      : []),
  ];

  const resolvedTab: HelpdeskTabId = visibleTabs.some((t) => t.id === activeTab)
    ? activeTab
    : "my-tickets";
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
      setActiveTab(target.id);
      tabRefs.current[target.id]?.focus();
    }
  };

  const selectedTicketId = useMemo(() => {
    if (manualSelectedTicketId) return manualSelectedTicketId;
    if (deepLinkedTicketId) return deepLinkedTicketId;
    if (resolvedTab === "staff-queue") return staffQueue[0]?.id ?? null;
    return myTickets[0]?.id ?? null;
  }, [manualSelectedTicketId, deepLinkedTicketId, resolvedTab, staffQueue, myTickets]);

  const handleOpenTicket = (ticketId: string) => {
    setManualSelectedTicketId(ticketId);
    if (resolvedTab === "assignment-rules") {
      setActiveTab(canHandleTickets ? "staff-queue" : "my-tickets");
    }
  };

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      {/* Top Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-appBorder pb-4">
        <div className="flex items-center gap-2.5">
          <LifeBuoy className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
          <div>
            <h1 className="text-xl font-bold text-[#102C54]">HR &amp; IT Helpdesk</h1>
            <p className="text-xs text-textSecondary">
              Read-only Knowledge Base assistant, ticket SLA tracking, and escalation management
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline">SLA Timers Active</Badge>
          <Button
            type="button"
            size="sm"
            disabled={!hydrated || isOffline}
            onClick={() => setIsNewModalOpen(true)}
            className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs inline-flex items-center gap-1.5"
          >
            <PlusCircle className="h-3.5 w-3.5" aria-hidden="true" />+ New Ticket
          </Button>
        </div>
      </div>

      {isOffline && (
        <StandbyBanner
          message="FastAPI backend is offline. Helpdesk ticket operations and SLA rules require an active backend connection."
          onRetry={() => {
            refetchHealth();
            refetchUser();
            refetchMyTickets();
            if (canHandleTickets) refetchQueue();
            if (canManageRules) refetchRules();
          }}
          isRetrying={
            isHealthFetching ||
            (hydrated && (isMyTicketsFetching || isQueueFetching || isRulesFetching))
          }
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

      {/* Read-Only Helpdesk AI Assistant */}
      {hydrated && <HelpdeskAskCard isOffline={isOffline} onOpenTicket={handleOpenTicket} />}

      {!hydrated ? (
        <Card className="border border-appBorder bg-white">
          <CardContent className="py-12 text-center text-xs text-textSecondary">
            Initializing authenticated Helpdesk workspace...
          </CardContent>
        </Card>
      ) : (
        <>
          {/* WAI-ARIA Tab Strip */}
          <div
            role="tablist"
            aria-label="Helpdesk workspace sections"
            className="flex flex-wrap gap-2 border-b border-appBorder pb-3"
          >
            {visibleTabs.map((tab, idx) => {
              const isSelected = resolvedTab === tab.id;
              return (
                <button
                  key={tab.id}
                  id={tab.domId}
                  ref={(el) => {
                    tabRefs.current[tab.id] = el;
                  }}
                  type="button"
                  role="tab"
                  aria-selected={isSelected}
                  tabIndex={isSelected ? 0 : -1}
                  onClick={() => setActiveTab(tab.id)}
                  onKeyDown={(e) => handleTabKeyDown(e, idx)}
                  className={`inline-flex items-center gap-2 rounded-small px-3.5 py-2 text-xs font-semibold transition-colors ${
                    isSelected
                      ? "bg-[#2A5BDB] text-white shadow-sm"
                      : "border border-appBorder bg-white text-[#102C54] hover:bg-[#F8F9FD]"
                  }`}
                >
                  <span>{tab.label}</span>
                  {typeof tab.count === "number" && (
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                        isSelected ? "bg-white/20 text-white" : "bg-slate-100 text-[#102C54]"
                      }`}
                    >
                      {tab.count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Active Tab Panel */}
          <div role="tabpanel" aria-labelledby={activeTabMeta.domId}>
            {resolvedTab === "my-tickets" && (
              <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] gap-4 items-start">
                <Card className="border border-appBorder bg-white shadow-sm">
                  <CardHeader>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <CardTitle className="text-base text-[#102C54]">
                          My Support Tickets ({myTickets.length})
                        </CardTitle>
                        <CardDescription className="text-xs">
                          Track submitted requests, reply to HR/IT handlers, rate resolved tickets,
                          or reopen within the allowed window.
                        </CardDescription>
                      </div>
                      <select
                        aria-label="Filter my tickets by status"
                        value={myStatusFilter}
                        onChange={(e) => setMyStatusFilter(e.target.value as TicketStatus | "")}
                        className="rounded-small border border-appBorder bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
                      >
                        <option value="">All Statuses</option>
                        {TICKET_STATUSES.map((st) => (
                          <option key={st} value={st}>
                            {STATUS_LABELS[st]}
                          </option>
                        ))}
                      </select>
                    </div>
                  </CardHeader>

                  <CardContent className="space-y-2.5">
                    {isMyTicketsLoading ? (
                      <p className="py-8 text-center text-xs text-textSecondary">
                        Loading your tickets...
                      </p>
                    ) : myTickets.length === 0 ? (
                      <div className="rounded-small border border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
                        You have not submitted any Helpdesk tickets yet.
                      </div>
                    ) : (
                      myTickets.map((ticket) => {
                        const isSelected = ticket.id === selectedTicketId;
                        return (
                          <div
                            key={ticket.id}
                            className={`rounded-medium border p-3.5 transition-colors ${
                              isSelected
                                ? "border-[#2A5BDB] bg-blue-50/50"
                                : "border-appBorder bg-[#F8F9FD] hover:bg-white"
                            }`}
                          >
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div className="space-y-1.5 min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-1.5">
                                  <span className="font-mono text-xs font-bold text-[#2A5BDB]">
                                    {ticket.ticket_number}
                                  </span>
                                  <TicketStatusBadge status={ticket.status} />
                                  <TicketPriorityBadge priority={ticket.priority} />
                                  <TicketCategoryBadge category={ticket.category} />
                                  <TicketSlaBadge ticket={ticket} />
                                </div>
                                <p dir="auto" className="text-xs font-bold text-[#102C54] truncate">
                                  {ticket.subject}
                                </p>
                                <p className="text-[11px] text-textSecondary">
                                  SLA Due: {formatPKT(ticket.sla_due_at)}
                                </p>
                              </div>

                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={() => setManualSelectedTicketId(ticket.id)}
                                aria-label={`View ${ticket.ticket_number}`}
                                className="h-7 px-2.5 text-xs border-[#2A5BDB]/40 text-[#2A5BDB] hover:bg-blue-50"
                              >
                                View
                              </Button>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </CardContent>
                </Card>

                <TicketDetailDrawer
                  ticketId={selectedTicketId}
                  currentUserId={currentUser?.user_id}
                  canHandleTickets={canHandleTickets}
                  staffOptions={staffOptions}
                  isOffline={isOffline}
                />
              </div>
            )}

            {resolvedTab === "staff-queue" && canHandleTickets && (
              <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] gap-4 items-start">
                <StaffQueueTab
                  tickets={staffQueue}
                  isLoading={isQueueLoading}
                  selectedTicketId={selectedTicketId}
                  filters={staffFilters}
                  onFiltersChange={setStaffFilters}
                  onSelectTicket={(id) => setManualSelectedTicketId(id)}
                />

                <TicketDetailDrawer
                  ticketId={selectedTicketId}
                  currentUserId={currentUser?.user_id}
                  canHandleTickets={canHandleTickets}
                  staffOptions={staffOptions}
                  isOffline={isOffline}
                />
              </div>
            )}

            {resolvedTab === "assignment-rules" && canManageRules && (
              <AssignmentRulesTab
                rules={ticketRules}
                isLoading={isRulesLoading}
                staffOptions={staffOptions}
                isOffline={isOffline}
              />
            )}
          </div>
        </>
      )}

      <NewTicketModal
        isOpen={isNewModalOpen}
        isOffline={isOffline}
        onClose={() => setIsNewModalOpen(false)}
        onCreated={(id) => {
          setManualSelectedTicketId(id);
          setActiveTab("my-tickets");
        }}
      />
    </div>
  );
}

export default function HelpdeskPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6 bg-[#F8F9FD] pb-10">
          <Card className="border border-appBorder bg-white">
            <CardContent className="py-12 text-center text-xs text-textSecondary">
              Loading Helpdesk workspace...
            </CardContent>
          </Card>
        </div>
      }
    >
      <HelpdeskWorkspaceContent />
    </Suspense>
  );
}
