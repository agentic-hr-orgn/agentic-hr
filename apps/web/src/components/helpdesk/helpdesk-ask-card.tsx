"use client";

import React, { useState } from "react";
import { Bot, SendHorizontal, ShieldAlert, Ticket as TicketIcon, XCircle } from "lucide-react";
import type { HelpdeskAskResponse } from "@agentic-hr/contracts";
import { CitationChips } from "@/components/chat/citation-chips";
import {
  TicketCategoryBadge,
  TicketPriorityBadge,
} from "@/components/helpdesk/ticket-status-badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAskHelpdesk } from "@/hooks/use-tickets";
import { isOfflineError } from "@/lib/api-request";

const HELPDESK_QUICK_PROMPTS: Array<{ label: string; question: string }> = [
  {
    label: "VPN & SSO Reset Guide",
    question: "How do I reset my corporate VPN password?",
  },
  {
    label: "IT Equipment Request",
    question: "My laptop charger stopped working and needs replacement",
  },
];

interface HelpdeskAskCardProps {
  isOffline: boolean;
  onOpenTicket: (ticketId: string) => void;
}

export function HelpdeskAskCard({ isOffline, onOpenTicket }: HelpdeskAskCardProps) {
  const askMutation = useAskHelpdesk();
  const [question, setQuestion] = useState("");
  const [lastResult, setLastResult] = useState<HelpdeskAskResponse | null>(null);
  const [liveHttpError, setLiveHttpError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = question.trim();
    if (clean.length < 2 || askMutation.isPending || isOffline) return;

    setLiveHttpError(null);
    try {
      const res = await askMutation.mutateAsync({ question: clean });
      setLastResult(res);
    } catch (err: unknown) {
      if (!isOfflineError(err)) {
        setLiveHttpError(
          err instanceof Error ? err.message : "Failed to reach Helpdesk assistant."
        );
      }
    }
  };

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
            <div>
              <CardTitle className="text-base text-[#102C54]">
                Ask Helpdesk Knowledge Base
              </CardTitle>
              <CardDescription className="text-xs">
                Get instant IT, Facilities, or HR KB answers with verified citations—or auto-route
                unmatched issues to a Helpdesk ticket.
              </CardDescription>
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-1.5">
          {HELPDESK_QUICK_PROMPTS.map((item) => (
            <button
              key={item.label}
              type="button"
              disabled={askMutation.isPending || isOffline}
              onClick={() => setQuestion(item.question)}
              className="rounded-full border border-appBorder bg-[#F8F9FD] px-2.5 py-1 text-[11px] font-medium text-[#102C54] hover:border-[#2A5BDB] hover:text-[#2A5BDB] transition-colors disabled:opacity-50"
            >
              {item.label}
            </button>
          ))}
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            dir="auto"
            aria-label="Ask Helpdesk a question"
            placeholder="Ask IT/HR support in English, اردو, or Roman Urdu..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={isOffline}
            className="flex-1 rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none disabled:bg-slate-50"
          />
          <Button
            type="submit"
            disabled={isOffline || askMutation.isPending || question.trim().length < 2}
            className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs inline-flex items-center gap-1.5 shrink-0"
          >
            <SendHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
            {askMutation.isPending ? "Checking KB..." : "Ask Helpdesk"}
          </Button>
        </form>

        {liveHttpError && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-small border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900"
          >
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span>{liveHttpError}</span>
          </div>
        )}

        {lastResult && (
          <div className="rounded-medium border border-appBorder bg-[#F8F9FD] p-3.5 space-y-2.5">
            {lastResult.answered ? (
              <>
                <p
                  dir="auto"
                  className="text-xs leading-relaxed text-[#102C54] whitespace-pre-wrap"
                >
                  {lastResult.answer}
                </p>
                <CitationChips citations={lastResult.citations} />
              </>
            ) : (
              <div className="space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {lastResult.privileged_request_blocked ? (
                      <ShieldAlert className="h-4 w-4 text-amber-700" aria-hidden="true" />
                    ) : (
                      <TicketIcon className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                    )}
                    <span className="text-xs font-bold text-[#102C54]">
                      Ticket Created: {lastResult.ticket_number || lastResult.ticket?.ticket_number}
                    </span>
                    {lastResult.category && <TicketCategoryBadge category={lastResult.category} />}
                    {lastResult.priority && <TicketPriorityBadge priority={lastResult.priority} />}
                  </div>

                  {lastResult.ticket?.id && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => onOpenTicket(lastResult.ticket!.id)}
                      className="h-7 px-2.5 text-[11px] border-[#2A5BDB] text-[#2A5BDB] hover:bg-blue-50"
                    >
                      Open Ticket Detail
                    </Button>
                  )}
                </div>

                <p dir="auto" className="text-xs text-textSecondary leading-relaxed">
                  {lastResult.answer}
                </p>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
