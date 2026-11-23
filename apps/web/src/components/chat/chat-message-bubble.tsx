"use client";

import React, { useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  CalendarPlus,
  Check,
  HelpCircle,
  Ticket as TicketIcon,
  ThumbsDown,
  ThumbsUp,
  Wrench,
} from "lucide-react";
import type { ChatMessage, FeedbackRating } from "@agentic-hr/contracts";
import { CitationChips } from "@/components/chat/citation-chips";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatPKT } from "@/lib/format";

const STREAMING_NODE_LABELS: Record<string, string> = {
  classify: "Understanding your question...",
  rewrite: "Resolving conversation context...",
  retrieve: "Searching approved policies...",
  grade: "Verifying relevant policy clauses...",
  reformulate: "Refining bilingual search terms...",
  answer: "Drafting grounded answer...",
  verify: "Checking citations against source chunks...",
  tools: "Running server-verified account lookup...",
  handoff_leave: "Preparing structured leave intake...",
  abstain: "Opening an HR follow-up ticket...",
};

interface ChatMessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  streamingNode?: string | null;
  currentRating?: FeedbackRating | null;
  isFeedbackPending?: boolean;
  isAskHrPending?: boolean;
  isOffline?: boolean;
  onFeedback?: (
    messageId: string,
    rating: FeedbackRating,
    comment?: string
  ) => Promise<void> | void;
  onAskHr?: (messageId: string) => Promise<void> | void;
}

function asRecord(val: unknown): Record<string, unknown> | null {
  return val && typeof val === "object" && !Array.isArray(val)
    ? (val as Record<string, unknown>)
    : null;
}

export function ChatMessageBubble({
  message,
  isStreaming = false,
  streamingNode = null,
  currentRating = null,
  isFeedbackPending = false,
  isAskHrPending = false,
  isOffline = false,
  onFeedback,
  onAskHr,
}: ChatMessageBubbleProps) {
  const [showDownComment, setShowDownComment] = useState(false);
  const [downComment, setDownComment] = useState("");
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[82%] rounded-medium bg-[#2A5BDB] px-4 py-3 text-white shadow-sm">
          <p dir="auto" className="text-xs leading-relaxed whitespace-pre-wrap">
            {message.content}
          </p>
          <p className="mt-1.5 text-[10px] text-blue-100 text-right">
            {formatPKT(message.created_at)}
          </p>
        </div>
      </div>
    );
  }

  const handoffRoot = asRecord(message.handoff_payload);
  const handoffExtracted = asRecord(handoffRoot?.extracted) ?? handoffRoot;
  const confirmCard = asRecord(handoffRoot?.confirm_card);
  const handoffStatus = typeof handoffRoot?.status === "string" ? handoffRoot.status : null;
  const clarifyingQuestion =
    typeof handoffRoot?.clarifying_question === "string" ? handoffRoot.clarifying_question : null;
  const prefillUrl =
    typeof handoffRoot?.prefill_url === "string" && handoffRoot.prefill_url.trim()
      ? handoffRoot.prefill_url
      : "/leave";

  const leaveTypeLabel =
    (typeof confirmCard?.leave_type_name === "string" && confirmCard.leave_type_name) ||
    (typeof handoffExtracted?.leave_type_code === "string" && handoffExtracted.leave_type_code) ||
    null;
  const startDate =
    typeof handoffExtracted?.start_date === "string" ? handoffExtracted.start_date : null;
  const endDate = typeof handoffExtracted?.end_date === "string" ? handoffExtracted.end_date : null;
  const leaveReason = typeof handoffExtracted?.reason === "string" ? handoffExtracted.reason : null;
  const workingDays =
    confirmCard?.working_days !== undefined && confirmCard?.working_days !== null
      ? String(confirmCard.working_days)
      : null;

  const ticketHref = message.ticket_id
    ? `/helpdesk?ticket=${encodeURIComponent(message.ticket_id)}`
    : "/helpdesk";

  const streamingStatusText = streamingNode
    ? STREAMING_NODE_LABELS[streamingNode] || `Running ${streamingNode}...`
    : "Connecting to Policy Brain...";

  const handleDownSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!onFeedback) return;
    await onFeedback(message.id, "down", downComment.trim() || undefined);
    setShowDownComment(false);
    setDownComment("");
  };

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[90%] rounded-medium border border-appBorder bg-white px-4 py-3.5 text-[#102C54] shadow-sm space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-textSecondary">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-bold text-[#102C54]">Policy Brain</span>
            <Badge variant="outline" className="text-[10px] uppercase">
              {message.language}
            </Badge>
            {message.intent && (
              <Badge variant="secondary" className="text-[10px]">
                {message.intent}
              </Badge>
            )}
          </div>
          <span>{formatPKT(message.created_at)}</span>
        </div>

        {isStreaming && !message.content && (
          <p className="text-xs italic text-[#2A5BDB]" aria-live="polite">
            {streamingStatusText}
          </p>
        )}

        {(message.content || isStreaming) && (
          <div dir="auto" className="text-xs leading-relaxed text-[#102C54] whitespace-pre-wrap">
            {message.content}
            {isStreaming && (
              <span
                aria-label="Streaming response"
                className="ml-1 inline-block h-3.5 w-1.5 animate-pulse bg-[#2A5BDB] align-middle"
              />
            )}
          </div>
        )}

        {isStreaming && message.content && streamingNode && (
          <p className="text-[10px] text-textSecondary">{streamingStatusText}</p>
        )}

        {message.tool_calls && message.tool_calls.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {message.tool_calls.map((tc, idx) => (
              <span
                key={`${tc.tool_name}-${idx}`}
                className={`inline-flex items-center gap-1 rounded-small border px-2 py-0.5 text-[10px] font-medium ${
                  tc.status === "denied"
                    ? "border-amber-300 bg-amber-50 text-amber-900"
                    : tc.status === "error"
                      ? "border-rose-300 bg-rose-50 text-rose-900"
                      : "border-appBorder bg-[#F8F9FD] text-[#102C54]"
                }`}
              >
                <Wrench className="h-3 w-3 text-[#2A5BDB]" aria-hidden="true" />
                {tc.tool_name} ({tc.status})
              </span>
            ))}
          </div>
        )}

        <CitationChips citations={message.citations ?? []} />

        {(message.abstained || message.ticket_id) && (
          <div
            role="status"
            className="flex flex-wrap items-center justify-between gap-2 rounded-small border border-amber-300 bg-amber-50/70 px-3 py-2 text-xs text-amber-950"
          >
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 text-amber-700 shrink-0" aria-hidden="true" />
              <span>
                {message.abstained
                  ? "No verified policy clause covered this question. Escalated to HR."
                  : "HR follow-up ticket linked to this answer."}
              </span>
            </div>
            {message.ticket_number && (
              <Link
                href={ticketHref}
                className="inline-flex items-center gap-1 rounded-full border border-[#2A5BDB]/40 bg-white px-2.5 py-0.5 text-[11px] font-bold text-[#2A5BDB] hover:bg-blue-50"
              >
                <TicketIcon className="h-3 w-3" aria-hidden="true" />
                {message.ticket_number}
              </Link>
            )}
          </div>
        )}

        {handoffRoot && (
          <div className="rounded-small border border-[#2A5BDB]/30 bg-blue-50/50 p-3 space-y-2 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-bold text-[#102C54] inline-flex items-center gap-1.5">
                <CalendarPlus className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                Leave Request Handoff
              </span>
              {handoffStatus && (
                <Badge variant="outline" className="text-[10px] bg-white">
                  {handoffStatus === "ready_to_confirm"
                    ? "Ready to Confirm"
                    : handoffStatus === "needs_clarification"
                      ? "Needs Details"
                      : handoffStatus}
                </Badge>
              )}
            </div>

            {handoffStatus === "needs_clarification" && clarifyingQuestion && (
              <p dir="auto" className="text-amber-900 font-medium">
                {clarifyingQuestion}
              </p>
            )}

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[#102C54]">
              {leaveTypeLabel && (
                <span>
                  Type: <strong>{leaveTypeLabel}</strong>
                </span>
              )}
              {startDate && (
                <span>
                  Dates:{" "}
                  <strong>
                    {startDate}
                    {endDate && endDate !== startDate ? ` to ${endDate}` : ""}
                  </strong>
                </span>
              )}
              {workingDays && (
                <span>
                  Working days: <strong>{workingDays}</strong>
                </span>
              )}
              {leaveReason && (
                <span>
                  Reason: <strong>{leaveReason}</strong>
                </span>
              )}
            </div>

            <div>
              <Link
                href={prefillUrl}
                className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#2A5BDB] hover:underline"
              >
                Continue in Leave Workspace
                <ArrowRight className="h-3 w-3" aria-hidden="true" />
              </Link>
            </div>
          </div>
        )}

        {!isStreaming && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-appBorder/60 pt-2">
            <div className="flex items-center gap-1.5">
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label="Thumbs Up"
                aria-pressed={currentRating === "up"}
                disabled={isFeedbackPending || isOffline || !onFeedback}
                onClick={() => onFeedback?.(message.id, "up")}
                className={`h-7 px-2 text-[11px] inline-flex items-center gap-1 ${
                  currentRating === "up"
                    ? "border-emerald-500 bg-emerald-50 text-emerald-800"
                    : "text-textSecondary"
                }`}
              >
                <ThumbsUp className="h-3 w-3" aria-hidden="true" />
                Helpful
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label="Thumbs Down"
                aria-pressed={currentRating === "down"}
                disabled={isFeedbackPending || isOffline || !onFeedback}
                onClick={() => {
                  setShowDownComment(true);
                  void onFeedback?.(message.id, "down");
                }}
                className={`h-7 px-2 text-[11px] inline-flex items-center gap-1 ${
                  currentRating === "down"
                    ? "border-rose-500 bg-rose-50 text-rose-800"
                    : "text-textSecondary"
                }`}
              >
                <ThumbsDown className="h-3 w-3" aria-hidden="true" />
                Needs Improvement
              </Button>
            </div>

            {!message.ticket_id ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={isAskHrPending || isOffline || !onAskHr}
                onClick={() => onAskHr?.(message.id)}
                className="h-7 px-2.5 text-[11px] border-[#2A5BDB]/40 text-[#2A5BDB] hover:bg-blue-50 inline-flex items-center gap-1"
              >
                <HelpCircle className="h-3 w-3" aria-hidden="true" />
                {isAskHrPending ? "Escalating..." : "Ask HR"}
              </Button>
            ) : (
              <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700">
                <Check className="h-3.5 w-3.5" aria-hidden="true" />
                HR Ticket Created
              </span>
            )}
          </div>
        )}

        {showDownComment && !isStreaming && onFeedback && (
          <form
            onSubmit={handleDownSubmit}
            className="flex flex-wrap items-center gap-2 rounded-small border border-appBorder bg-[#F8F9FD] p-2"
          >
            <input
              type="text"
              dir="auto"
              aria-label="Feedback comment"
              placeholder="What was missing or unclear? (optional)"
              value={downComment}
              onChange={(e) => setDownComment(e.target.value)}
              className="flex-1 min-w-[180px] rounded-small border border-appBorder bg-white px-2.5 py-1 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
            />
            <Button
              type="submit"
              size="sm"
              disabled={isFeedbackPending || isOffline}
              className="h-7 px-2.5 text-[11px] bg-[#102C54] text-white hover:bg-[#2A5BDB]"
            >
              Submit Feedback
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowDownComment(false)}
              className="h-7 px-2 text-[11px]"
            >
              Close
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
