"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import {
  ChevronDown,
  ChevronRight,
  Lock,
  Search,
  Sparkles,
  Ticket as TicketIcon,
  Upload,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { usePolicyGaps } from "@/hooks/use-chat";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useHydrated } from "@/hooks/use-hydrated";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";

export default function PolicyGapsPage() {
  const hydrated = useHydrated();
  const {
    data: currentUser,
    isLoading: isUserLoading,
    error: userError,
    refetch: refetchUser,
    isFetching: isUserFetching,
  } = useCurrentUser();

  const isHrOrOwner = Boolean(
    currentUser && (currentUser.role === "owner" || currentUser.is_hr_head)
  );
  const canViewPolicyGaps = hydrated && isHrOrOwner;

  const {
    isStandby: isBackendStandby,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();

  const {
    data: gapsData,
    isLoading: isGapsLoading,
    error: gapsError,
    refetch: refetchGaps,
    isFetching: isGapsFetching,
  } = usePolicyGaps({ enabled: canViewPolicyGaps });

  const [searchQuery, setSearchQuery] = useState("");
  const [expandedTopics, setExpandedTopics] = useState<Record<string, boolean>>({});

  const isOffline =
    hydrated && (isBackendStandby || isOfflineError(userError) || isOfflineError(gapsError));

  const liveHttpError = useMemo(() => {
    if (!hydrated) return null;
    if (userError && !isOfflineError(userError)) return userError.message;
    if (gapsError && !isOfflineError(gapsError)) return gapsError.message;
    return null;
  }, [hydrated, userError, gapsError]);

  const groups = useMemo(() => gapsData?.groups ?? [], [gapsData?.groups]);
  const totalAbstained = groups.reduce((acc, g) => acc + g.abstained_count, 0);
  const totalDownvoted = groups.reduce((acc, g) => acc + g.downvoted_count, 0);
  const topGapTopic = groups[0]?.topic_tag ?? "None";

  const filteredGroups = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter(
      (g) =>
        g.topic_tag.toLowerCase().includes(q) ||
        g.sample_questions.some((question) => question.toLowerCase().includes(q)) ||
        g.ticket_numbers.some((tkt) => tkt.toLowerCase().includes(q))
    );
  }, [groups, searchQuery]);

  const toggleTopic = (topicTag: string) => {
    setExpandedTopics((prev) => ({ ...prev, [topicTag]: !prev[topicTag] }));
  };

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-appBorder pb-4">
        <div className="flex items-center gap-2.5">
          <Sparkles className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
          <div>
            <h1 className="text-xl font-bold text-[#102C54]">HR Policy Gaps</h1>
            <p className="text-xs text-textSecondary">
              Identify unanswered &amp; down-voted employee questions so People Ops can close
              handbook gaps
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline">Owner &amp; HR Head Only</Badge>
          {canViewPolicyGaps && (
            <Link
              href="/policies"
              className="inline-flex h-8 items-center gap-1.5 rounded-small bg-[#2A5BDB] px-3 text-xs font-semibold text-white hover:bg-[#102C54] transition-colors"
            >
              <Upload className="h-3.5 w-3.5" aria-hidden="true" />
              Upload / Update Policy
            </Link>
          )}
        </div>
      </div>

      {isOffline && (
        <StandbyBanner
          message="FastAPI backend is offline. Policy Gap insights require an active backend connection."
          onRetry={() => {
            refetchHealth();
            refetchUser();
            if (canViewPolicyGaps) {
              refetchGaps();
            }
          }}
          isRetrying={isHealthFetching || (hydrated && (isUserFetching || isGapsFetching))}
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

      {!hydrated || (isUserLoading && !currentUser) ? (
        <div className="space-y-4" aria-busy="true">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="h-24 rounded-medium border border-appBorder bg-white animate-pulse" />
            <div className="h-24 rounded-medium border border-appBorder bg-white animate-pulse" />
            <div className="h-24 rounded-medium border border-appBorder bg-white animate-pulse" />
            <div className="h-24 rounded-medium border border-appBorder bg-white animate-pulse" />
          </div>
          <div className="h-40 rounded-medium border border-appBorder bg-white animate-pulse" />
        </div>
      ) : currentUser && !isHrOrOwner ? (
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardHeader>
            <CardTitle className="text-base text-[#102C54] flex items-center gap-2">
              <Lock className="h-4 w-4 text-amber-600" aria-hidden="true" />
              Access Restricted
            </CardTitle>
            <CardDescription className="text-xs">
              HR Policy Gaps analytics are available exclusively to Organization Owners and HR
              Heads.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Link href="/chat">
              <Button size="sm" className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs">
                Open Policy Chat
              </Button>
            </Link>
            <Link href="/policies">
              <Button variant="outline" size="sm" className="text-xs">
                Browse Policy Library
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* KPI Summary Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <Card className="border border-appBorder bg-white shadow-sm">
              <CardHeader className="pb-2">
                <CardDescription className="text-xs">Total Policy Gaps</CardDescription>
                <CardTitle className="text-2xl text-[#102C54]">
                  {isGapsLoading ? "—" : (gapsData?.total_gaps ?? 0)}
                </CardTitle>
              </CardHeader>
            </Card>

            <Card className="border border-appBorder bg-white shadow-sm">
              <CardHeader className="pb-2">
                <CardDescription className="text-xs">Abstained Questions</CardDescription>
                <CardTitle className="text-2xl text-amber-700">
                  {isGapsLoading ? "—" : totalAbstained}
                </CardTitle>
              </CardHeader>
            </Card>

            <Card className="border border-appBorder bg-white shadow-sm">
              <CardHeader className="pb-2">
                <CardDescription className="text-xs">Down-Voted Answers</CardDescription>
                <CardTitle className="text-2xl text-rose-700">
                  {isGapsLoading ? "—" : totalDownvoted}
                </CardTitle>
              </CardHeader>
            </Card>

            <Card className="border border-appBorder bg-white shadow-sm">
              <CardHeader className="pb-2">
                <CardDescription className="text-xs">Top Gap Topic</CardDescription>
                <CardTitle className="text-lg text-[#2A5BDB] truncate">{topGapTopic}</CardTitle>
              </CardHeader>
            </Card>
          </div>

          {/* Search & Grouped Topic Accordion */}
          <Card className="border border-appBorder bg-white shadow-sm">
            <CardHeader>
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-base text-[#102C54]">
                    Knowledge Gaps by Topic ({filteredGroups.length})
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Expand any topic to review actual employee questions and linked HR tickets.
                  </CardDescription>
                </div>

                <div role="search" className="relative w-full md:w-72">
                  <Search
                    className="h-3.5 w-3.5 text-textMuted absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
                    aria-hidden="true"
                  />
                  <input
                    type="search"
                    dir="auto"
                    aria-label="Filter policy gap topics"
                    placeholder="Filter topics, questions, or tickets..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full rounded-small border border-appBorder bg-white pl-8 pr-3 py-1.5 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none"
                  />
                </div>
              </div>
            </CardHeader>

            <CardContent className="space-y-3">
              {isGapsLoading ? (
                <p className="py-8 text-center text-xs text-textSecondary">
                  Aggregating abstained and down-voted policy queries...
                </p>
              ) : filteredGroups.length === 0 ? (
                <div className="rounded-small border border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
                  {groups.length === 0
                    ? "No unanswered or down-voted policy questions recorded yet."
                    : "No policy gap topics matched your filter."}
                </div>
              ) : (
                filteredGroups.map((group, idx) => {
                  const isExpanded = expandedTopics[group.topic_tag] ?? idx === 0;
                  return (
                    <div
                      key={group.topic_tag}
                      className="rounded-medium border border-appBorder bg-[#F8F9FD] overflow-hidden"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 bg-white">
                        <button
                          type="button"
                          onClick={() => toggleTopic(group.topic_tag)}
                          aria-expanded={isExpanded}
                          className="flex items-center gap-2 text-left min-w-0 flex-1"
                        >
                          {isExpanded ? (
                            <ChevronDown className="h-4 w-4 text-[#2A5BDB] shrink-0" />
                          ) : (
                            <ChevronRight className="h-4 w-4 text-textSecondary shrink-0" />
                          )}
                          <span className="text-xs font-bold text-[#102C54]">
                            {group.topic_tag}
                          </span>
                          <Badge variant="outline" className="text-[10px]">
                            {group.total_count} total
                          </Badge>
                          <Badge variant="warning" className="text-[10px]">
                            {group.abstained_count} abstained
                          </Badge>
                          <Badge
                            variant="outline"
                            className="text-[10px] border-rose-300 bg-rose-50 text-rose-800"
                          >
                            {group.downvoted_count} down-voted
                          </Badge>
                        </button>

                        <div className="flex items-center gap-2.5">
                          <span className="text-[11px] text-textSecondary">
                            Latest: {formatPKT(group.latest_at)}
                          </span>
                          <Link
                            href="/policies"
                            className="inline-flex h-7 items-center rounded-small border border-[#2A5BDB]/40 bg-white px-2.5 text-[11px] font-semibold text-[#2A5BDB] hover:bg-blue-50 transition-colors"
                          >
                            Upload / Update Policy
                          </Link>
                        </div>
                      </div>

                      {isExpanded && (
                        <div className="border-t border-appBorder px-4 py-3 space-y-2.5">
                          <div className="space-y-1.5">
                            <p className="text-[11px] font-semibold text-[#102C54]">
                              Employee Questions ({group.sample_questions.length})
                            </p>
                            {group.sample_questions.length === 0 ? (
                              <p className="text-[11px] text-textSecondary">
                                No question snippets captured.
                              </p>
                            ) : (
                              <ul className="space-y-1.5">
                                {group.sample_questions.map((question, qIdx) => (
                                  <li
                                    key={`${group.topic_tag}-q-${qIdx}`}
                                    dir="auto"
                                    className="rounded-small border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54]"
                                  >
                                    {question}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>

                          {group.ticket_numbers.length > 0 && (
                            <div className="flex flex-wrap items-center gap-1.5 pt-1">
                              <span className="text-[11px] font-semibold text-textSecondary mr-1">
                                Linked Tickets:
                              </span>
                              {group.ticket_numbers.map((tktNum) => (
                                <Link
                                  key={tktNum}
                                  href="/helpdesk"
                                  className="inline-flex items-center gap-1 rounded-full border border-[#2A5BDB]/30 bg-white px-2.5 py-0.5 text-[11px] font-semibold text-[#2A5BDB] hover:bg-blue-50"
                                >
                                  <TicketIcon className="h-3 w-3" aria-hidden="true" />
                                  {tktNum}
                                </Link>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
