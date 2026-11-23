"use client";

import React, { useMemo } from "react";
import { AlertTriangle, Search } from "lucide-react";
import type { Ticket, TicketCategory, TicketPriority, TicketStatus } from "@agentic-hr/contracts";
import {
  CATEGORY_LABELS,
  PRIORITY_LABELS,
  STATUS_LABELS,
  TICKET_CATEGORIES,
  TICKET_PRIORITIES,
  TICKET_STATUSES,
  TicketCategoryBadge,
  TicketPriorityBadge,
  TicketSlaBadge,
  TicketStatusBadge,
  isTicketSlaBreached,
  useClientNowMs,
} from "@/components/helpdesk/ticket-status-badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatPKT } from "@/lib/format";

export interface StaffQueueFilters {
  status: TicketStatus | "";
  category: TicketCategory | "";
  priority: TicketPriority | "";
  search: string;
}

interface StaffQueueTabProps {
  tickets: Ticket[];
  isLoading: boolean;
  selectedTicketId: string | null;
  filters: StaffQueueFilters;
  onFiltersChange: (next: StaffQueueFilters) => void;
  onSelectTicket: (ticketId: string) => void;
}

export function StaffQueueTab({
  tickets,
  isLoading,
  selectedTicketId,
  filters,
  onFiltersChange,
  onSelectTicket,
}: StaffQueueTabProps) {
  const nowMs = useClientNowMs();

  const filteredTickets = useMemo(() => {
    const q = filters.search.trim().toLowerCase();
    return tickets.filter((t) => {
      if (filters.status && t.status !== filters.status) return false;
      if (filters.category && t.category !== filters.category) return false;
      if (filters.priority && t.priority !== filters.priority) return false;
      if (!q) return true;
      return (
        t.ticket_number.toLowerCase().includes(q) ||
        t.subject.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q)
      );
    });
  }, [tickets, filters]);

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader className="space-y-3">
        <div>
          <CardTitle className="text-base text-[#102C54]">
            Helpdesk Staff Queue ({filteredTickets.length})
          </CardTitle>
          <CardDescription className="text-xs">
            Triage employee requests, watch SLA breach risks, and post public replies or internal
            notes.
          </CardDescription>
        </div>

        <div role="search" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
          <div className="relative">
            <Search
              className="h-3.5 w-3.5 text-textMuted absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
              aria-hidden="true"
            />
            <input
              type="search"
              dir="auto"
              aria-label="Search staff queue"
              placeholder="Search TKT- or subject..."
              value={filters.search}
              onChange={(e) => onFiltersChange({ ...filters, search: e.target.value })}
              className="w-full rounded-small border border-appBorder bg-white pl-8 pr-2.5 py-1.5 text-xs text-[#102C54]"
            />
          </div>

          <select
            aria-label="Filter queue by status"
            value={filters.status}
            onChange={(e) =>
              onFiltersChange({ ...filters, status: e.target.value as TicketStatus | "" })
            }
            className="rounded-small border border-appBorder bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
          >
            <option value="">All Statuses</option>
            {TICKET_STATUSES.map((st) => (
              <option key={st} value={st}>
                {STATUS_LABELS[st]}
              </option>
            ))}
          </select>

          <select
            aria-label="Filter queue by category"
            value={filters.category}
            onChange={(e) =>
              onFiltersChange({ ...filters, category: e.target.value as TicketCategory | "" })
            }
            className="rounded-small border border-appBorder bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
          >
            <option value="">All Categories</option>
            {TICKET_CATEGORIES.map((cat) => (
              <option key={cat} value={cat}>
                {CATEGORY_LABELS[cat]}
              </option>
            ))}
          </select>

          <select
            aria-label="Filter queue by priority"
            value={filters.priority}
            onChange={(e) =>
              onFiltersChange({ ...filters, priority: e.target.value as TicketPriority | "" })
            }
            className="rounded-small border border-appBorder bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
          >
            <option value="">All Priorities</option>
            {TICKET_PRIORITIES.map((pri) => (
              <option key={pri} value={pri}>
                {PRIORITY_LABELS[pri]}
              </option>
            ))}
          </select>
        </div>
      </CardHeader>

      <CardContent className="space-y-2.5">
        {isLoading ? (
          <p className="py-8 text-center text-xs text-textSecondary">Loading staff queue...</p>
        ) : filteredTickets.length === 0 ? (
          <div className="rounded-small border border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
            No tickets matched the active queue filters.
          </div>
        ) : (
          filteredTickets.map((ticket) => {
            const isSelected = ticket.id === selectedTicketId;
            const isBreached = isTicketSlaBreached(ticket, nowMs);

            return (
              <div
                key={ticket.id}
                className={`rounded-medium border p-3.5 transition-colors ${
                  isSelected
                    ? "border-[#2A5BDB] bg-blue-50/50"
                    : isBreached
                      ? "border-rose-300 bg-rose-50/40"
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
                      {isBreached && (
                        <AlertTriangle className="h-3.5 w-3.5 text-rose-600" aria-hidden="true" />
                      )}
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
                    onClick={() => onSelectTicket(ticket.id)}
                    aria-label={`Inspect ${ticket.ticket_number}`}
                    className="h-7 px-2.5 text-xs border-[#2A5BDB]/40 text-[#2A5BDB] hover:bg-blue-50"
                  >
                    Inspect
                  </Button>
                </div>
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}
