"use client";

import React, { useEffect, useId, useState } from "react";
import { PlusCircle, X, XCircle } from "lucide-react";
import type { TicketCategory, TicketPriority } from "@agentic-hr/contracts";
import {
  CATEGORY_LABELS,
  PRIORITY_LABELS,
  TICKET_CATEGORIES,
  TICKET_PRIORITIES,
} from "@/components/helpdesk/ticket-status-badges";
import { Button } from "@/components/ui/button";
import { useCreateTicket } from "@/hooks/use-tickets";
import { isOfflineError } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

interface NewTicketModalProps {
  isOpen: boolean;
  isOffline: boolean;
  onClose: () => void;
  onCreated: (ticketId: string) => void;
}

export function NewTicketModal({ isOpen, isOffline, onClose, onCreated }: NewTicketModalProps) {
  const titleId = useId();
  const categoryId = useId();
  const priorityId = useId();
  const subjectId = useId();
  const descId = useId();

  const createTicket = useCreateTicket();
  const pushToast = useToastStore((s) => s.pushToast);

  const [category, setCategory] = useState<TicketCategory>("hr_policy");
  const [priority, setPriority] = useState<TicketPriority>("medium");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (createTicket.isPending || isOffline) return;
    setErrorMessage(null);

    if (subject.trim().length < 3 || description.trim().length < 3) {
      setErrorMessage("Subject and description must each be at least 3 characters.");
      return;
    }

    try {
      const created = await createTicket.mutateAsync({
        category,
        priority,
        subject: subject.trim(),
        description: description.trim(),
        source: "manual",
      });
      pushToast({
        variant: "success",
        title: `Ticket Created (${created.ticket_number})`,
        description: `Routed to ${CATEGORY_LABELS[created.category]} support.`,
      });
      setSubject("");
      setDescription("");
      onCreated(created.id);
      onClose();
    } catch (err: unknown) {
      if (!isOfflineError(err)) {
        setErrorMessage(err instanceof Error ? err.message : "Failed to create ticket.");
      } else {
        setErrorMessage("FastAPI backend is offline; ticket could not be created.");
      }
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-lg rounded-medium border border-appBorder bg-white shadow-xl overflow-hidden"
      >
        <div className="flex items-center justify-between border-b border-appBorder bg-[#F8F9FD] px-5 py-3.5">
          <h2 id={titleId} className="text-sm font-bold text-[#102C54] flex items-center gap-2">
            <PlusCircle className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
            Create Support Ticket
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close modal"
            className="rounded-small p-1 text-textSecondary hover:bg-slate-200 hover:text-[#102C54]"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {errorMessage && (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-small border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900"
            >
              <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
              <span>{errorMessage}</span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label
                htmlFor={categoryId}
                className="block text-xs font-semibold text-[#102C54] mb-1"
              >
                Category
              </label>
              <select
                id={categoryId}
                value={category}
                onChange={(e) => setCategory(e.target.value as TicketCategory)}
                className="w-full rounded-small border border-appBorder bg-white px-2.5 py-2 text-xs text-[#102C54]"
              >
                {TICKET_CATEGORIES.map((cat) => (
                  <option key={cat} value={cat}>
                    {CATEGORY_LABELS[cat]}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor={priorityId}
                className="block text-xs font-semibold text-[#102C54] mb-1"
              >
                Priority
              </label>
              <select
                id={priorityId}
                value={priority}
                onChange={(e) => setPriority(e.target.value as TicketPriority)}
                className="w-full rounded-small border border-appBorder bg-white px-2.5 py-2 text-xs text-[#102C54]"
              >
                {TICKET_PRIORITIES.map((pri) => (
                  <option key={pri} value={pri}>
                    {PRIORITY_LABELS[pri]}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label htmlFor={subjectId} className="block text-xs font-semibold text-[#102C54] mb-1">
              Subject
            </label>
            <input
              id={subjectId}
              type="text"
              dir="auto"
              placeholder="Brief summary of your issue..."
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="w-full rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
            />
          </div>

          <div>
            <label htmlFor={descId} className="block text-xs font-semibold text-[#102C54] mb-1">
              Description
            </label>
            <textarea
              id={descId}
              dir="auto"
              rows={4}
              placeholder="Provide full context so our HR/IT team can resolve this quickly..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
            />
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-appBorder pt-3">
            <Button type="button" variant="outline" size="sm" onClick={onClose} className="text-xs">
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={createTicket.isPending || isOffline}
              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs"
            >
              {createTicket.isPending ? "Submitting..." : "Submit Ticket"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
