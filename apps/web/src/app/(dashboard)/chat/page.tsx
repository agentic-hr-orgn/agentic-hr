"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { BookOpenCheck, Bot, XCircle } from "lucide-react";
import type { ChatMessage, FeedbackRating, PolicyBrainStreamEvent } from "@agentic-hr/contracts";
import { ChatComposer } from "@/components/chat/chat-composer";
import { ChatHistorySidebar } from "@/components/chat/chat-history-sidebar";
import { ChatMessageBubble } from "@/components/chat/chat-message-bubble";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { useBackendStatus } from "@/hooks/use-backend-status";
import {
  useAskHrFromMessage,
  useChatMessages,
  useChatSessions,
  useCreateChatSession,
  useDeleteChatSession,
  useStreamChat,
  useSubmitAnswerFeedback,
} from "@/hooks/use-chat";
import { useHydrated } from "@/hooks/use-hydrated";
import { isOfflineError } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

interface PendingStreamTurn {
  sessionId: string;
  userMessage: ChatMessage;
  assistantMessage: ChatMessage;
  isStreaming: boolean;
  streamingNode: string | null;
  completedMessageId: string | null;
}

export default function PolicyChatPage() {
  const hydrated = useHydrated();
  const pushToast = useToastStore((s) => s.pushToast);

  const {
    isStandby: isBackendStandby,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();

  const {
    data: sessions = [],
    isLoading: isSessionsLoading,
    error: sessionsError,
    refetch: refetchSessions,
    isFetching: isSessionsFetching,
  } = useChatSessions();

  const sortedSessions = useMemo(() => {
    return [...sessions].sort((a, b) => {
      const aTs = new Date(a.last_message_at || a.updated_at || a.created_at).getTime();
      const bTs = new Date(b.last_message_at || b.updated_at || b.created_at).getTime();
      return bTs - aTs;
    });
  }, [sessions]);

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const activeSessionId = useMemo(() => {
    if (selectedSessionId && sortedSessions.some((s) => s.id === selectedSessionId)) {
      return selectedSessionId;
    }
    return sortedSessions[0]?.id ?? null;
  }, [selectedSessionId, sortedSessions]);

  const {
    data: persistedMessages = [],
    isLoading: isMessagesLoading,
    error: messagesError,
    refetch: refetchMessages,
    isFetching: isMessagesFetching,
  } = useChatMessages(activeSessionId);

  const createSession = useCreateChatSession();
  const deleteSession = useDeleteChatSession();
  const submitFeedback = useSubmitAnswerFeedback();
  const askHr = useAskHrFromMessage();
  const { streamChat, isStreaming, abort } = useStreamChat();

  const [composerValue, setComposerValue] = useState("");
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [askHrPendingId, setAskHrPendingId] = useState<string | null>(null);
  const [ratingsByMsgId, setRatingsByMsgId] = useState<Record<string, FeedbackRating>>({});
  const [pendingTurn, setPendingTurn] = useState<PendingStreamTurn | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);
  const bottomAnchorRef = useRef<HTMLDivElement | null>(null);

  const isOffline =
    hydrated &&
    (isBackendStandby ||
      isOfflineError(sessionsError) ||
      isOfflineError(messagesError) ||
      isOfflineError(actionError));

  const liveHttpError = useMemo(() => {
    if (!hydrated) return null;
    if (actionError && !isOfflineError(actionError)) return actionError.message;
    if (sessionsError && !isOfflineError(sessionsError)) return sessionsError.message;
    if (messagesError && !isOfflineError(messagesError)) return messagesError.message;
    return null;
  }, [hydrated, actionError, sessionsError, messagesError]);

  const activeSession = sortedSessions.find((s) => s.id === activeSessionId) ?? null;

  const displayMessages = useMemo(() => {
    const base = hydrated ? persistedMessages : [];
    if (!pendingTurn || pendingTurn.sessionId !== activeSessionId) {
      return base;
    }
    const persistedIds = new Set(base.map((m) => m.id));
    if (pendingTurn.completedMessageId && persistedIds.has(pendingTurn.completedMessageId)) {
      return base;
    }
    const lastPersistedUser = [...base].reverse().find((m) => m.role === "user");
    const userAlreadyPersisted = Boolean(
      lastPersistedUser && lastPersistedUser.content === pendingTurn.userMessage.content
    );
    return userAlreadyPersisted
      ? [...base, pendingTurn.assistantMessage]
      : [...base, pendingTurn.userMessage, pendingTurn.assistantMessage];
  }, [hydrated, persistedMessages, pendingTurn, activeSessionId]);

  useEffect(() => {
    if (!hydrated) return;
    if (bottomAnchorRef.current && typeof bottomAnchorRef.current.scrollIntoView === "function") {
      bottomAnchorRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [hydrated, displayMessages.length, pendingTurn?.assistantMessage.content]);

  const handleSelectSession = (sessionId: string) => {
    if (sessionId === activeSessionId) return;
    abort();
    setPendingTurn(null);
    setActionError(null);
    setSelectedSessionId(sessionId);
  };

  const handleCreateSession = async () => {
    if (createSession.isPending || isOffline) return;
    abort();
    setPendingTurn(null);
    setActionError(null);
    try {
      const created = await createSession.mutateAsync({ title: "New conversation" });
      setSelectedSessionId(created.id);
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err : new Error("Failed to create chat session."));
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    if (isOffline) return;
    if (sessionId === activeSessionId) {
      abort();
      setPendingTurn(null);
    }
    setActionError(null);
    setDeletingSessionId(sessionId);
    try {
      await deleteSession.mutateAsync(sessionId);
      if (sessionId === selectedSessionId) {
        setSelectedSessionId(null);
      }
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err : new Error("Failed to delete chat session."));
    } finally {
      setDeletingSessionId(null);
    }
  };

  const handleSendMessage = async () => {
    const question = composerValue.trim();
    if (!question || isStreaming || createSession.isPending || isOffline) return;

    setActionError(null);
    setComposerValue("");

    let targetSessionId = activeSessionId;
    try {
      if (!targetSessionId) {
        const created = await createSession.mutateAsync({
          title: question.slice(0, 80),
        });
        targetSessionId = created.id;
        setSelectedSessionId(created.id);
      }
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err : new Error("Failed to create chat session."));
      setComposerValue(question);
      return;
    }

    const nowIso = new Date().toISOString();
    const tempUserMsg: ChatMessage = {
      id: `optimistic-user-${Date.now()}`,
      tenant_id: activeSession?.tenant_id ?? "",
      session_id: targetSessionId,
      role: "user",
      content: question,
      language: "en",
      citations: [],
      tool_calls: [],
      abstained: false,
      created_at: nowIso,
    };
    const tempAssistantId = `optimistic-assistant-${Date.now()}`;
    const tempAssistantMsg: ChatMessage = {
      id: tempAssistantId,
      tenant_id: activeSession?.tenant_id ?? "",
      session_id: targetSessionId,
      role: "assistant",
      content: "",
      language: "en",
      citations: [],
      tool_calls: [],
      abstained: false,
      created_at: nowIso,
    };

    setPendingTurn({
      sessionId: targetSessionId,
      userMessage: tempUserMsg,
      assistantMessage: tempAssistantMsg,
      isStreaming: true,
      streamingNode: "classify",
      completedMessageId: null,
    });

    try {
      let streamErrorDetail: string | null = null;

      const streamRes = await streamChat({
        sessionId: targetSessionId,
        payload: { message: question },
        onEvent: (event: PolicyBrainStreamEvent) => {
          if (event.type === "status") {
            setPendingTurn((prev) =>
              prev && prev.sessionId === targetSessionId
                ? { ...prev, streamingNode: event.data.node }
                : prev
            );
          } else if (event.type === "token") {
            setPendingTurn((prev) =>
              prev && prev.sessionId === targetSessionId
                ? {
                    ...prev,
                    assistantMessage: {
                      ...prev.assistantMessage,
                      content: `${prev.assistantMessage.content}${event.data.text}`,
                    },
                  }
                : prev
            );
          } else if (event.type === "done") {
            const d = event.data;
            setPendingTurn((prev) =>
              prev && prev.sessionId === targetSessionId
                ? {
                    ...prev,
                    isStreaming: false,
                    streamingNode: null,
                    completedMessageId: d.message_id,
                    assistantMessage: {
                      ...prev.assistantMessage,
                      id: d.message_id || tempAssistantId,
                      content: d.answer,
                      language: d.language,
                      intent: d.intent,
                      citations: d.citations ?? [],
                      tool_calls: d.tool_calls ?? [],
                      abstained: Boolean(d.abstained),
                      ticket_id: d.ticket_id ?? null,
                      ticket_number: d.ticket_number ?? null,
                      handoff_payload: d.handoff_payload ?? null,
                      topic_tag: d.topic_tag ?? null,
                      latency_ms: d.latency_ms ?? null,
                    },
                  }
                : prev
            );
          } else if (event.type === "error") {
            streamErrorDetail = event.data.detail || "Streaming error occurred.";
          }
        },
      });

      if (streamErrorDetail) {
        setActionError(new Error(streamErrorDetail));
      }

      if (streamRes.aborted) {
        setPendingTurn((prev) =>
          prev && prev.sessionId === targetSessionId ? { ...prev, isStreaming: false } : prev
        );
        return;
      }

      if (!streamRes.donePayload && !streamErrorDetail) {
        setPendingTurn((prev) =>
          prev && prev.sessionId === targetSessionId ? { ...prev, isStreaming: false } : prev
        );
      }
    } catch (err: unknown) {
      setActionError(
        err instanceof Error ? err : new Error("Failed to stream Policy Brain answer.")
      );
      setPendingTurn((prev) =>
        prev && prev.sessionId === targetSessionId && !prev.assistantMessage.content
          ? null
          : prev && prev.sessionId === targetSessionId
            ? { ...prev, isStreaming: false, streamingNode: null }
            : prev
      );
    }
  };

  const handleFeedback = async (messageId: string, rating: FeedbackRating, comment?: string) => {
    if (isOffline) return;
    setActionError(null);
    setRatingsByMsgId((prev) => ({ ...prev, [messageId]: rating }));
    try {
      await submitFeedback.mutateAsync({
        messageId,
        payload: { rating, comment: comment ?? null },
      });
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err : new Error("Failed to save answer feedback."));
    }
  };

  const handleAskHr = async (messageId: string) => {
    if (isOffline || askHr.isPending) return;
    setActionError(null);
    setAskHrPendingId(messageId);
    try {
      const created = await askHr.mutateAsync({ messageId });
      setPendingTurn((prev) =>
        prev && prev.assistantMessage.id === messageId
          ? {
              ...prev,
              assistantMessage: {
                ...prev.assistantMessage,
                ticket_id: created.ticket_id,
                ticket_number: created.ticket_number,
              },
            }
          : prev
      );
      pushToast({
        variant: "success",
        title: `HR Ticket Created (${created.ticket_number})`,
        description: "Escalated to the People team for follow-up.",
        actionLabel: "Open Helpdesk",
        actionHref: `/helpdesk?ticket=${encodeURIComponent(created.ticket_id)}`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to escalate message to HR.";
      setActionError(new Error(msg));
      pushToast({ variant: "error", title: "Ask HR escalation failed", description: msg });
    } finally {
      setAskHrPendingId(null);
    }
  };

  return (
    <div className="space-y-5 bg-[#F8F9FD] pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-appBorder pb-4">
        <div className="flex items-center gap-2.5">
          <BookOpenCheck className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
          <div>
            <h1 className="text-xl font-bold text-[#102C54]">Policy Brain Chat</h1>
            <p className="text-xs text-textSecondary">
              Grounded bilingual policy answers with verified clause citations &amp; safe tool
              lookups
            </p>
          </div>
        </div>
        <Badge variant="outline">Grounded Citations Enabled</Badge>
      </div>

      {isOffline && (
        <StandbyBanner
          message="FastAPI backend is offline. Policy Brain streaming chat requires a live backend connection."
          onRetry={() => {
            refetchHealth();
            refetchSessions();
            if (activeSessionId) {
              refetchMessages();
            }
          }}
          isRetrying={isHealthFetching || (hydrated && (isSessionsFetching || isMessagesFetching))}
        />
      )}

      {liveHttpError && (
        <div
          role="alert"
          className="flex items-center justify-between gap-2 rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{liveHttpError}</span>
          </div>
          {actionError && (
            <button
              type="button"
              onClick={() => setActionError(null)}
              aria-label="Dismiss error"
              className="px-1 font-bold text-rose-700 hover:text-rose-900"
            >
              &times;
            </button>
          )}
        </div>
      )}

      {!hydrated ? (
        <Card className="border border-appBorder bg-white">
          <CardContent className="py-12 text-center text-xs text-textSecondary">
            Initializing authenticated Policy Brain workspace...
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[290px_minmax(0,1fr)] gap-4 items-start">
          <ChatHistorySidebar
            sessions={sortedSessions}
            activeSessionId={activeSessionId}
            isLoading={isSessionsLoading}
            isCreating={createSession.isPending}
            isDeletingId={deletingSessionId}
            isOffline={isOffline}
            onSelectSession={handleSelectSession}
            onCreateSession={() => void handleCreateSession()}
            onDeleteSession={(id) => void handleDeleteSession(id)}
          />

          <Card className="border border-appBorder bg-white shadow-sm flex flex-col overflow-hidden">
            <CardHeader className="border-b border-appBorder bg-white py-3.5 px-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <CardTitle className="text-sm text-[#102C54] truncate">
                    {activeSession?.title || "Ask Policy Brain"}
                  </CardTitle>
                  <CardDescription className="text-[11px]">
                    Every policy claim is verified against active handbook chunks or escalated to
                    HR.
                  </CardDescription>
                </div>
                {activeSession && (
                  <Badge variant="outline" className="text-[10px] uppercase">
                    {activeSession.language}
                  </Badge>
                )}
              </div>
            </CardHeader>

            <CardContent className="p-0 flex flex-col">
              <div
                aria-label="Conversation messages"
                className="min-h-[380px] max-h-[540px] overflow-y-auto p-4 space-y-4 bg-[#F8F9FD]/60"
              >
                {activeSessionId && isMessagesLoading ? (
                  <p className="text-center text-xs text-textSecondary py-10">
                    Loading conversation history...
                  </p>
                ) : displayMessages.length === 0 ? (
                  <div className="mx-auto max-w-md py-12 text-center space-y-2.5">
                    <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-blue-50 text-[#2A5BDB]">
                      <Bot className="h-5 w-5" aria-hidden="true" />
                    </div>
                    <h2 className="text-sm font-bold text-[#102C54]">
                      Ask any company policy, leave, or HR question
                    </h2>
                    <p className="text-xs text-textSecondary">
                      Supports English, اردو, and Roman Urdu. Pick a starter prompt below or type
                      your own question.
                    </p>
                  </div>
                ) : (
                  displayMessages.map((msg) => {
                    const isActiveStreamMsg = Boolean(
                      pendingTurn?.isStreaming && pendingTurn.assistantMessage.id === msg.id
                    );
                    return (
                      <ChatMessageBubble
                        key={msg.id}
                        message={msg}
                        isStreaming={isActiveStreamMsg}
                        streamingNode={isActiveStreamMsg ? pendingTurn?.streamingNode : null}
                        currentRating={ratingsByMsgId[msg.id] ?? null}
                        isFeedbackPending={submitFeedback.isPending}
                        isAskHrPending={askHrPendingId === msg.id}
                        isOffline={isOffline}
                        onFeedback={handleFeedback}
                        onAskHr={handleAskHr}
                      />
                    );
                  })
                )}
                <div ref={bottomAnchorRef} />
              </div>

              <ChatComposer
                value={composerValue}
                onChange={setComposerValue}
                onSend={() => void handleSendMessage()}
                onAbort={() => {
                  abort();
                  setPendingTurn((prev) => (prev ? { ...prev, isStreaming: false } : null));
                }}
                isStreaming={isStreaming || Boolean(pendingTurn?.isStreaming)}
                isBusy={createSession.isPending}
                isOffline={isOffline}
              />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
