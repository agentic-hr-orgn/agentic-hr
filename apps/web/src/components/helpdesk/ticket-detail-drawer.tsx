"use client";

import React, { useId, useState } from "react";
import {
  Clock,
  Lock,
  MessageSquare,
  RotateCcw,
  Save,
  Send,
  Star,
  UserCheck,
  X,
  XCircle,
} from "lucide-react";
import type { TicketDetail, TicketPriority, TicketStatus } from "@agentic-hr/contracts";
import {
  PRIORITY_LABELS,
  STATUS_LABELS,
  TICKET_PRIORITIES,
  TICKET_STATUSES,
  TicketCategoryBadge,
  TicketPriorityBadge,
  TicketSlaBadge,
  TicketStatusBadge,
  useClientNowMs,
  type StaffAssigneeOption,
} from "@/components/helpdesk/ticket-status-badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useAddTicketMessage,
  useRateTicket,
  useReopenTicket,
  useTicketDetail,
  useUpdateTicket,
} from "@/hooks/use-tickets";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";
import { useToastStore } from "@/stores/toast-store";

interface TicketDetailDrawerProps {
  ticketId: string | null;
  currentUserId?: string;
  canHandleTickets: boolean;
  staffOptions: StaffAssigneeOption[];
  isOffline: boolean;
  onClose?: () => void;
}

interface TicketDetailBodyProps {
  ticket: TicketDetail;
  currentUserId?: string;
  canHandleTickets: boolean;
  staffOptions: StaffAssigneeOption[];
  isOffline: boolean;
}

function TicketDetailBody({
  ticket,
  currentUserId,
  canHandleTickets,
  staffOptions,
  isOffline,
}: TicketDetailBodyProps) {
  const nowMs = useClientNowMs();
  const pushToast = useToastStore((s) => s.pushToast);

  const updateTicket = useUpdateTicket();
  const addMessage = useAddTicketMessage();
  const rateTicket = useRateTicket();
  const reopenTicket = useReopenTicket();

  const statusSelectId = useId();
  const prioritySelectId = useId();
  const assigneeSelectId = useId();
  const replyInputId = useId();
  const internalNoteCheckId = useId();
  const reopenReasonId = useId();

  const [staffStatus, setStaffStatus] = useState<TicketStatus>(ticket.status);
  const [staffPriority, setStaffPriority] = useState<TicketPriority>(ticket.priority);
  const [staffAssignee, setStaffAssignee] = useState<string>(ticket.assigned_to_user_id ?? "");

  const [replyBody, setReplyBody] = useState("");
  const [isInternalNote, setIsInternalNote] = useState(false);

  const [selectedRating, setSelectedRating] = useState<number>(ticket.satisfaction_rating ?? 5);
  const [ratingComment, setRatingComment] = useState("");
  const [reopenReason, setReopenReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const isRequester = !currentUserId || ticket.requester_user_id === currentUserId;
  const isResolvedOrClosed = ticket.status === "resolved" || ticket.status === "closed";

  const reopenExpiryMs = ticket.reopen_until ? Date.parse(ticket.reopen_until) : Number.NaN;
  const isReopenExpired = Boolean(
    nowMs !== null &&
    ticket.reopen_until &&
    Number.isFinite(reopenExpiryMs) &&
    nowMs > reopenExpiryMs
  );

  // Defensive visibility enforcement: plain employees NEVER see internal notes
  const visibleMessages = (ticket.messages ?? []).filter((m) =>
    canHandleTickets ? true : !m.is_internal
  );

  const assigneeMatch = staffOptions.find((emp) => emp.user_id === ticket.assigned_to_user_id);
  const assigneeLabel = assigneeMatch
    ? assigneeMatch.full_name
    : ticket.assigned_to_user_id
      ? ticket.assigned_to_user_id
      : "Unassigned";

  const handleSaveStaffControls = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canHandleTickets || updateTicket.isPending || isOffline) return;
    setActionError(null);
    try {
      await updateTicket.mutateAsync({
        ticketId: ticket.id,
        payload: {
          status: staffStatus,
          priority: staffPriority,
          assigned_to_user_id: staffAssignee ? staffAssignee : null,
        },
      });
      pushToast({
        variant: "success",
        title: `Updated ${ticket.ticket_number}`,
        description: `Status: ${STATUS_LABELS[staffStatus]}, Priority: ${PRIORITY_LABELS[staffPriority]}.`,
      });
    } catch (err: unknown) {
      if (!isOfflineError(err)) {
        setActionError(err instanceof Error ? err.message : "Failed to update ticket.");
      }
    }
  };

  const handlePostReply = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = replyBody.trim();
    if (!clean || addMessage.isPending || isOffline) return;
    setActionError(null);
    try {
      await addMessage.mutateAsync({
        ticketId: ticket.id,
        payload: {
          body: clean,
          is_internal: canHandleTickets ? isInternalNote : false,
        },
      });
      setReplyBody("");
      setIsInternalNote(false);
    } catch (err: unknown) {
      if (!isOfflineError(err)) {
        setActionError(err instanceof Error ? err.message : "Failed to post ticket message.");
      }
    }
  };

  const handleRateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (rateTicket.isPending || isOffline) return;
    setActionError(null);
    try {
      await rateTicket.mutateAsync({
        ticketId: ticket.id,
        payload: {
          satisfaction_rating: selectedRating,
          satisfaction_comment: ratingComment.trim() || null,
        },
      });
      pushToast({
        variant: "success",
        title: `Thank you for rating ${ticket.ticket_number}`,
        description: `Recorded ${selectedRating}/5 satisfaction score.`,
      });
    } catch (err: unknown) {
      if (!isOfflineError(err)) {
        setActionError(err instanceof Error ? err.message : "Failed to submit ticket rating.");
      }
    }
  };

  const handleReopenSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanReason = reopenReason.trim();
    if (cleanReason.length < 3 || reopenTicket.isPending || isOffline || isReopenExpired) return;
    setActionError(null);
    try {
      await reopenTicket.mutateAsync({
        ticketId: ticket.id,
        payload: { reason: cleanReason },
      });
      setReopenReason("");
      pushToast({
        variant: "success",
        title: `Reopened ${ticket.ticket_number}`,
        description: "The ticket is now open again and SLA tracking has resumed.",
      });
    } catch (err: unknown) {
      if (!isOfflineError(err)) {
        setActionError(err instanceof Error ? err.message : "Failed to reopen ticket.");
      }
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-bold text-[#2A5BDB]">{ticket.ticket_number}</span>
        <TicketStatusBadge status={ticket.status} />
        <TicketPriorityBadge priority={ticket.priority} />
        <TicketCategoryBadge category={ticket.category} />
        <TicketSlaBadge ticket={ticket} />
      </div>

      <div>
        <h3 dir="auto" className="text-sm font-bold text-[#102C54]">
          {ticket.subject}
        </h3>
        <p
          dir="auto"
          className="mt-1.5 rounded-small border border-appBorder bg-[#F8F9FD] px-3 py-2.5 text-xs text-[#102C54] leading-relaxed whitespace-pre-wrap"
        >
          {ticket.description}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] text-textSecondary rounded-small border border-appBorder bg-white p-3">
        <div className="flex items-center gap-1.5">
          <Clock className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
          <span>
            SLA Due: <strong className="text-[#102C54]">{formatPKT(ticket.sla_due_at)}</strong>
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <UserCheck className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
          <span>
            Assignee: <strong className="text-[#102C54]">{assigneeLabel}</strong>
          </span>
        </div>
        <div>
          Source: <strong className="text-[#102C54]">{ticket.source}</strong>
        </div>
        <div>
          Created: <strong className="text-[#102C54]">{formatPKT(ticket.created_at)}</strong>
        </div>
      </div>

      {actionError && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-small border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900"
        >
          <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
          <span>{actionError}</span>
        </div>
      )}

      {canHandleTickets && (
        <form
          onSubmit={handleSaveStaffControls}
          className="rounded-small border border-[#2A5BDB]/30 bg-blue-50/40 p-3 space-y-2.5"
        >
          <p className="text-xs font-bold text-[#102C54]">Helpdesk Staff Controls</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div>
              <label
                htmlFor={statusSelectId}
                className="block text-[11px] font-semibold text-[#102C54] mb-1"
              >
                Status
              </label>
              <select
                id={statusSelectId}
                value={staffStatus}
                onChange={(e) => setStaffStatus(e.target.value as TicketStatus)}
                disabled={isOffline}
                className="w-full rounded-small border border-appBorder bg-white px-2 py-1.5 text-xs text-[#102C54]"
              >
                {TICKET_STATUSES.map((st) => (
                  <option key={st} value={st}>
                    {STATUS_LABELS[st]}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor={prioritySelectId}
                className="block text-[11px] font-semibold text-[#102C54] mb-1"
              >
                Priority
              </label>
              <select
                id={prioritySelectId}
                value={staffPriority}
                onChange={(e) => setStaffPriority(e.target.value as TicketPriority)}
                disabled={isOffline}
                className="w-full rounded-small border border-appBorder bg-white px-2 py-1.5 text-xs text-[#102C54]"
              >
                {TICKET_PRIORITIES.map((pri) => (
                  <option key={pri} value={pri}>
                    {PRIORITY_LABELS[pri]}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor={assigneeSelectId}
                className="block text-[11px] font-semibold text-[#102C54] mb-1"
              >
                Assignee
              </label>
              <select
                id={assigneeSelectId}
                value={staffAssignee}
                onChange={(e) => setStaffAssignee(e.target.value)}
                disabled={isOffline}
                className="w-full rounded-small border border-appBorder bg-white px-2 py-1.5 text-xs text-[#102C54]"
              >
                <option value="">Unassigned</option>
                {staffOptions.map((emp) => (
                  <option key={emp.user_id} value={emp.user_id}>
                    {emp.full_name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex justify-end">
            <Button
              type="submit"
              size="sm"
              disabled={updateTicket.isPending || isOffline}
              className="h-7 px-3 text-xs bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1"
            >
              <Save className="h-3 w-3" aria-hidden="true" />
              {updateTicket.isPending ? "Updating..." : "Save Changes"}
            </Button>
          </div>
        </form>
      )}

      {/* Conversation Thread */}
      <div className="space-y-2">
        <p className="text-xs font-bold text-[#102C54] flex items-center gap-1.5">
          <MessageSquare className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
          Ticket Thread ({visibleMessages.length})
        </p>

        {visibleMessages.length === 0 ? (
          <p className="rounded-small border border-appBorder bg-[#F8F9FD] px-3 py-4 text-center text-xs text-textSecondary">
            No replies yet.
          </p>
        ) : (
          <div className="space-y-2 max-h-[260px] overflow-y-auto pr-1">
            {visibleMessages.map((msg) => (
              <div
                key={msg.id}
                className={`rounded-small border p-3 space-y-1 text-xs ${
                  msg.is_internal ? "border-amber-300 bg-amber-50/70" : "border-appBorder bg-white"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-textSecondary">
                  <div className="flex items-center gap-1.5">
                    {msg.is_internal && (
                      <Badge
                        variant="warning"
                        className="text-[10px] inline-flex items-center gap-1"
                      >
                        <Lock className="h-2.5 w-2.5" aria-hidden="true" />
                        Internal Only
                      </Badge>
                    )}
                    <span>{formatPKT(msg.created_at)}</span>
                  </div>
                </div>
                <p dir="auto" className="text-[#102C54] whitespace-pre-wrap leading-relaxed">
                  {msg.body}
                </p>
              </div>
            ))}
          </div>
        )}

        <form onSubmit={handlePostReply} className="space-y-2 pt-1">
          <label htmlFor={replyInputId} className="sr-only">
            Ticket reply message
          </label>
          <textarea
            id={replyInputId}
            dir="auto"
            rows={2}
            placeholder={
              canHandleTickets && isInternalNote
                ? "Write an internal staff-only note (hidden from requester)..."
                : "Write a reply..."
            }
            value={replyBody}
            onChange={(e) => setReplyBody(e.target.value)}
            disabled={isOffline}
            className="w-full rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
          />

          <div className="flex flex-wrap items-center justify-between gap-2">
            {canHandleTickets ? (
              <label
                htmlFor={internalNoteCheckId}
                className="inline-flex items-center gap-2 text-xs text-[#102C54] cursor-pointer"
              >
                <input
                  id={internalNoteCheckId}
                  type="checkbox"
                  checked={isInternalNote}
                  onChange={(e) => setIsInternalNote(e.target.checked)}
                  className="rounded border-appBorder accent-amber-600"
                />
                Internal Note (Staff Only)
              </label>
            ) : (
              <span />
            )}

            <Button
              type="submit"
              size="sm"
              disabled={addMessage.isPending || isOffline || !replyBody.trim()}
              className="h-8 px-3 text-xs bg-[#102C54] text-white hover:bg-[#2A5BDB] inline-flex items-center gap-1"
            >
              <Send className="h-3 w-3" aria-hidden="true" />
              {addMessage.isPending
                ? "Posting..."
                : canHandleTickets && isInternalNote
                  ? "Add Internal Note"
                  : "Send Reply"}
            </Button>
          </div>
        </form>
      </div>

      {/* Requester CSAT Close Rating & Window-Enforced Reopen */}
      {isResolvedOrClosed && isRequester && (
        <div className="space-y-3 border-t border-appBorder pt-3">
          {ticket.satisfaction_rating ? (
            <div className="rounded-small border border-emerald-200 bg-emerald-50/60 px-3 py-2.5 text-xs text-emerald-950">
              <p className="font-semibold">
                Satisfaction Rating Recorded: {ticket.satisfaction_rating} / 5
              </p>
              {ticket.satisfaction_comment && (
                <p dir="auto" className="mt-0.5 text-[11px] opacity-90">
                  {ticket.satisfaction_comment}
                </p>
              )}
            </div>
          ) : (
            <form
              onSubmit={handleRateSubmit}
              className="rounded-small border border-appBorder bg-[#F8F9FD] p-3 space-y-2.5"
            >
              <p className="text-xs font-bold text-[#102C54]">Rate Support Experience (1–5)</p>
              <div className="flex items-center gap-1">
                {[1, 2, 3, 4, 5].map((star) => (
                  <button
                    key={star}
                    type="button"
                    aria-label={`Rate ${star} star${star > 1 ? "s" : ""}`}
                    aria-pressed={selectedRating === star}
                    onClick={() => setSelectedRating(star)}
                    className={`rounded-small p-1.5 transition-colors ${
                      star <= selectedRating
                        ? "text-amber-500 bg-amber-50 border border-amber-200"
                        : "text-slate-300 bg-white border border-appBorder"
                    }`}
                  >
                    <Star className="h-4 w-4 fill-current" aria-hidden="true" />
                  </button>
                ))}
                <span className="ml-2 text-xs font-semibold text-[#102C54]">
                  {selectedRating} / 5
                </span>
              </div>
              <input
                type="text"
                dir="auto"
                aria-label="Satisfaction comment"
                placeholder="Optional feedback comment..."
                value={ratingComment}
                onChange={(e) => setRatingComment(e.target.value)}
                disabled={isOffline}
                className="w-full rounded-small border border-appBorder bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
              />
              <Button
                type="submit"
                size="sm"
                disabled={rateTicket.isPending || isOffline}
                className="h-7 px-3 text-xs bg-[#2A5BDB] text-white hover:bg-[#102C54]"
              >
                {rateTicket.isPending ? "Submitting..." : "Submit Rating"}
              </Button>
            </form>
          )}

          <form
            onSubmit={handleReopenSubmit}
            className="rounded-small border border-appBorder bg-white p-3 space-y-2"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-bold text-[#102C54]">Need More Help?</p>
              {ticket.reopen_until && (
                <span className="text-[11px] text-textSecondary">
                  Reopen window ends: {formatPKT(ticket.reopen_until)}
                </span>
              )}
            </div>

            {isReopenExpired ? (
              <p className="text-[11px] text-amber-800">
                The reopen window for this ticket has expired. Please open a new ticket instead.
              </p>
            ) : (
              <div className="flex flex-col sm:flex-row gap-2">
                <label htmlFor={reopenReasonId} className="sr-only">
                  Reopen reason
                </label>
                <input
                  id={reopenReasonId}
                  type="text"
                  dir="auto"
                  placeholder="Reason for reopening (min 3 chars)..."
                  value={reopenReason}
                  onChange={(e) => setReopenReason(e.target.value)}
                  disabled={isOffline}
                  className="flex-1 rounded-small border border-appBorder bg-white px-2.5 py-1.5 text-xs text-[#102C54]"
                />
                <Button
                  type="submit"
                  variant="outline"
                  size="sm"
                  disabled={
                    reopenTicket.isPending ||
                    isOffline ||
                    isReopenExpired ||
                    reopenReason.trim().length < 3
                  }
                  className="h-8 px-3 text-xs inline-flex items-center gap-1"
                >
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                  {reopenTicket.isPending ? "Reopening..." : "Reopen Ticket"}
                </Button>
              </div>
            )}
          </form>
        </div>
      )}
    </div>
  );
}

export function TicketDetailDrawer({
  ticketId,
  currentUserId,
  canHandleTickets,
  staffOptions,
  isOffline,
  onClose,
}: TicketDetailDrawerProps) {
  const {
    data: ticket,
    isLoading,
    error,
  } = useTicketDetail(ticketId, {
    enabled: Boolean(ticketId),
  });

  if (!ticketId) {
    return (
      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="text-sm text-[#102C54]">Ticket Detail</CardTitle>
          <CardDescription className="text-xs">
            Select any ticket from the list to view SLA timers, thread replies, and actions.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader className="border-b border-appBorder pb-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm text-[#102C54]">Ticket Inspection</CardTitle>
            <CardDescription className="text-[11px]">
              Real-time SLA status, replies, and resolution controls.
            </CardDescription>
          </div>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close ticket detail"
              className="rounded-small p-1 text-textSecondary hover:bg-slate-100 hover:text-[#102C54]"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>
      </CardHeader>

      <CardContent className="pt-4">
        {isLoading ? (
          <p className="py-8 text-center text-xs text-textSecondary">Loading ticket details...</p>
        ) : error && !isOfflineError(error) ? (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-small border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900"
          >
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span>{error.message}</span>
          </div>
        ) : !ticket ? (
          <p className="py-8 text-center text-xs text-textSecondary">Ticket not found.</p>
        ) : (
          <TicketDetailBody
            key={`${ticket.id}:${ticket.updated_at}`}
            ticket={ticket}
            currentUserId={currentUserId}
            canHandleTickets={canHandleTickets}
            staffOptions={staffOptions}
            isOffline={isOffline}
          />
        )}
      </CardContent>
    </Card>
  );
}
