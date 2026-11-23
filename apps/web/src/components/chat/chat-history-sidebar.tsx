"use client";

import React, { useMemo, useState } from "react";
import { MessageSquarePlus, Search, Trash2, MessagesSquare } from "lucide-react";
import type { ChatSession } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatPKT } from "@/lib/format";

interface ChatHistorySidebarProps {
  sessions: ChatSession[];
  activeSessionId: string | null;
  isLoading: boolean;
  isCreating: boolean;
  isDeletingId: string | null;
  isOffline: boolean;
  onSelectSession: (sessionId: string) => void;
  onCreateSession: () => void;
  onDeleteSession: (sessionId: string) => void;
}

export function ChatHistorySidebar({
  sessions,
  activeSessionId,
  isLoading,
  isCreating,
  isDeletingId,
  isOffline,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
}: ChatHistorySidebarProps) {
  const [filterText, setFilterText] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const sortedSessions = useMemo(() => {
    const sorted = [...sessions].sort((a, b) => {
      const aTime = new Date(a.last_message_at || a.updated_at || a.created_at).getTime();
      const bTime = new Date(b.last_message_at || b.updated_at || b.created_at).getTime();
      return bTime - aTime;
    });
    const q = filterText.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter((s) => s.title.toLowerCase().includes(q));
  }, [sessions, filterText]);

  return (
    <aside
      aria-label="Chat history"
      className="flex flex-col rounded-medium border border-appBorder bg-white shadow-sm overflow-hidden"
    >
      <div className="border-b border-appBorder p-3.5 space-y-3 bg-[#F8F9FD]">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-bold text-[#102C54] flex items-center gap-1.5">
            <MessagesSquare className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
            Conversations
          </h2>
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setConfirmDeleteId(null);
              onCreateSession();
            }}
            disabled={isCreating || isOffline}
            className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs h-8 px-2.5 inline-flex items-center gap-1"
          >
            <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden="true" />+ New Chat
          </Button>
        </div>

        <div role="search" className="relative">
          <Search
            className="h-3.5 w-3.5 text-textMuted absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
            aria-hidden="true"
          />
          <input
            type="search"
            dir="auto"
            aria-label="Filter conversations"
            placeholder="Search conversations..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="w-full rounded-small border border-appBorder bg-white pl-8 pr-2.5 py-1.5 text-xs text-[#102C54] placeholder:text-textMuted focus:border-[#2A5BDB] focus:outline-none"
          />
        </div>
      </div>

      <div className="flex-1 divide-y divide-appBorder/60 max-h-[540px] overflow-y-auto p-2 space-y-1">
        {isLoading ? (
          <div className="space-y-2 p-2" aria-busy="true">
            <div className="h-12 rounded-small bg-slate-100 animate-pulse" />
            <div className="h-12 rounded-small bg-slate-100 animate-pulse" />
          </div>
        ) : sortedSessions.length === 0 ? (
          <div className="px-3 py-8 text-center text-xs text-textSecondary">
            {sessions.length === 0
              ? "No conversations yet. Click + New Chat or ask a policy question below."
              : "No matching conversations."}
          </div>
        ) : (
          sortedSessions.map((session) => {
            const isActive = session.id === activeSessionId;
            const isConfirming = confirmDeleteId === session.id;
            const isDeleting = isDeletingId === session.id;
            const timestamp = session.last_message_at || session.updated_at || session.created_at;

            return (
              <div
                key={session.id}
                className={`rounded-small border p-2.5 transition-colors ${
                  isActive
                    ? "border-[#2A5BDB] bg-blue-50/70"
                    : "border-transparent hover:border-appBorder hover:bg-[#F8F9FD]"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setConfirmDeleteId(null);
                      onSelectSession(session.id);
                    }}
                    className="min-w-0 flex-1 text-left"
                    aria-current={isActive ? "true" : undefined}
                  >
                    <p
                      dir="auto"
                      className={`text-xs font-semibold truncate ${
                        isActive ? "text-[#2A5BDB]" : "text-[#102C54]"
                      }`}
                    >
                      {session.title || "New conversation"}
                    </p>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-textSecondary">
                      <span>{formatPKT(timestamp)}</span>
                      <Badge variant="outline" className="text-[9px] uppercase px-1 py-0">
                        {session.language}
                      </Badge>
                    </div>
                  </button>

                  {!isConfirming && (
                    <button
                      type="button"
                      onClick={() => setConfirmDeleteId(session.id)}
                      disabled={isDeleting || isOffline}
                      aria-label={`Delete conversation ${session.title}`}
                      className="rounded-small p-1 text-textMuted hover:bg-rose-50 hover:text-rose-700 disabled:opacity-40"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  )}
                </div>

                {isConfirming && (
                  <div className="mt-2 flex items-center justify-end gap-1.5 border-t border-appBorder/60 pt-2">
                    <span className="text-[10px] text-rose-800 mr-auto font-medium">
                      Delete chat?
                    </span>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setConfirmDeleteId(null)}
                      disabled={isDeleting}
                      className="h-6 px-2 text-[10px]"
                    >
                      Cancel
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      disabled={isDeleting || isOffline}
                      onClick={() => {
                        setConfirmDeleteId(null);
                        onDeleteSession(session.id);
                      }}
                      className="h-6 px-2 text-[10px] bg-rose-600 text-white hover:bg-rose-700"
                    >
                      {isDeleting ? "Deleting..." : "Confirm"}
                    </Button>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
