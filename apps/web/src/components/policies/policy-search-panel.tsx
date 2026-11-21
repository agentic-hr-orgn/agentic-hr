"use client";

import React, { useId, useRef, useState } from "react";
import Link from "next/link";
import {
  BookOpenCheck,
  ExternalLink,
  Languages,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import {
  VisibilityBadge,
  formatDocType,
  formatLanguageLabel,
} from "@/components/policies/policy-status-badges";
import { useSearchPolicies } from "@/hooks/use-policies";
import { isOfflineError } from "@/lib/api-request";

interface PolicySearchPanelProps {
  userRole: string;
  isHrHead?: boolean;
  isBackendOffline?: boolean;
}

const DOC_TYPE_FILTERS: Array<{
  value: "all" | "company_policy" | "labour_law" | "helpdesk_knowledge";
  label: string;
}> = [
  { value: "all", label: "All Types" },
  { value: "company_policy", label: "Company Policy" },
  { value: "labour_law", label: "Labour Law" },
  { value: "helpdesk_knowledge", label: "Helpdesk Knowledge" },
];

const LANGUAGE_FILTERS: Array<{
  value: "all" | "en" | "ur" | "mixed";
  label: string;
}> = [
  { value: "all", label: "All Languages" },
  { value: "en", label: "English (EN)" },
  { value: "ur", label: "Urdu (UR)" },
  { value: "mixed", label: "Bilingual (Mixed)" },
];

export function PolicySearchPanel({
  userRole,
  isHrHead = false,
  isBackendOffline = false,
}: PolicySearchPanelProps) {
  const queryInputId = useId();
  const queryErrorId = useId();
  const queryInputRef = useRef<HTMLInputElement | null>(null);

  const searchMutation = useSearchPolicies();

  const [query, setQuery] = useState("");
  const [docTypeFilter, setDocTypeFilter] = useState<
    "all" | "company_policy" | "labour_law" | "helpdesk_knowledge"
  >("all");
  const [languageFilter, setLanguageFilter] = useState<"all" | "en" | "ur" | "mixed">("all");
  const [validationError, setValidationError] = useState<string | null>(null);

  const roleLabel =
    userRole.toLowerCase() === "owner"
      ? "Owner"
      : isHrHead
        ? "HR Head"
        : userRole.toLowerCase() === "manager"
          ? "Manager"
          : "Employee";

  const isOffline = isBackendOffline || isOfflineError(searchMutation.error);
  const liveHttpError =
    searchMutation.error && !isOfflineError(searchMutation.error)
      ? searchMutation.error.message
      : null;

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setValidationError("Enter at least 2 characters to search policy clauses.");
      queryInputRef.current?.focus();
      return;
    }

    setValidationError(null);
    try {
      await searchMutation.mutateAsync({
        query: trimmed,
        top_k: 8,
        doc_type: docTypeFilter === "all" ? null : docTypeFilter,
        language: languageFilter === "all" ? null : languageFilter,
      });
    } catch {
      // Handled via searchMutation.error state with offline vs HTTP alert discrimination
    }
  };

  const results = searchMutation.data;

  return (
    <div className="space-y-5">
      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <div className="h-8 w-8 rounded-medium bg-[#2A5BDB]/10 flex items-center justify-center text-[#2A5BDB]">
                <Languages className="h-4 w-4" aria-hidden="true" />
              </div>
              <div>
                <CardTitle className="text-base text-[#102C54]">
                  Bilingual Hybrid Policy Search (English &amp; Urdu)
                </CardTitle>
                <CardDescription className="text-xs">
                  Hybrid vector (pgvector HNSW), keyword (simple FTS), and trigram retrieval across
                  active published handbooks.
                </CardDescription>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="primary" className="gap-1 text-[11px]">
                <ShieldCheck className="h-3 w-3 shrink-0" aria-hidden="true" />
                Searching active policies visible to your role ({roleLabel})
              </Badge>
              <Badge variant="outline" className="gap-1 text-[11px]">
                <Sparkles className="h-3 w-3 shrink-0 text-[#2A5BDB]" aria-hidden="true" />
                Retrieval preview — cited Q&amp;A arrives in Stage 7
              </Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isOffline && (
            <StandbyBanner message="FastAPI backend is offline. Live bilingual search requires an active backend connection." />
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

          <div role="search" aria-label="Search corporate policies">
            <form onSubmit={(e) => void handleSubmit(e)} noValidate className="space-y-4">
              <div>
                <label
                  htmlFor={queryInputId}
                  className="block text-xs font-semibold text-[#102C54] mb-1.5"
                >
                  Search Query (English or Urdu / اردو)
                </label>
                <div className="flex flex-col sm:flex-row gap-2">
                  <input
                    id={queryInputId}
                    ref={queryInputRef}
                    name="query"
                    type="search"
                    dir="auto"
                    value={query}
                    autoCorrect="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    enterKeyHint="search"
                    aria-invalid={validationError ? "true" : undefined}
                    aria-describedby={validationError ? queryErrorId : undefined}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && e.nativeEvent.isComposing) {
                        e.preventDefault();
                      }
                    }}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      if (validationError && e.target.value.trim().length >= 2) {
                        setValidationError(null);
                      }
                    }}
                    placeholder="Ask in English or Urdu, e.g., annual leave carry forward or سالانہ چھٹی..."
                    className="flex-1 rounded-small border border-appBorder bg-white px-3.5 py-2.5 text-sm text-[#102C54] placeholder:text-textMuted focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                  />
                  <Button
                    type="submit"
                    disabled={searchMutation.isPending || isBackendOffline}
                    className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs px-5 inline-flex items-center justify-center gap-1.5 shrink-0"
                  >
                    <Search className="h-3.5 w-3.5" aria-hidden="true" />
                    {searchMutation.isPending ? "Searching..." : "Search Policies"}
                  </Button>
                </div>
                {validationError && (
                  <p
                    id={queryErrorId}
                    role="alert"
                    className="mt-1.5 text-xs font-medium text-rose-700"
                  >
                    {validationError}
                  </p>
                )}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
                <fieldset>
                  <legend className="text-[11px] font-semibold text-textSecondary mb-1.5">
                    Document Type
                  </legend>
                  <div className="flex flex-wrap gap-1.5">
                    {DOC_TYPE_FILTERS.map((opt) => {
                      const active = docTypeFilter === opt.value;
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setDocTypeFilter(opt.value)}
                          className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                            active
                              ? "border-[#2A5BDB] bg-[#2A5BDB] text-white"
                              : "border-appBorder bg-[#F8F9FD] text-[#102C54] hover:border-[#2A5BDB]/50"
                          }`}
                        >
                          {opt.label}
                        </button>
                      );
                    })}
                  </div>
                </fieldset>

                <fieldset>
                  <legend className="text-[11px] font-semibold text-textSecondary mb-1.5">
                    Language Filter
                  </legend>
                  <div className="flex flex-wrap gap-1.5">
                    {LANGUAGE_FILTERS.map((opt) => {
                      const active = languageFilter === opt.value;
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setLanguageFilter(opt.value)}
                          className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                            active
                              ? "border-[#102C54] bg-[#102C54] text-white"
                              : "border-appBorder bg-[#F8F9FD] text-[#102C54] hover:border-[#102C54]/50"
                          }`}
                        >
                          {opt.label}
                        </button>
                      );
                    })}
                  </div>
                </fieldset>
              </div>
            </form>
          </div>
        </CardContent>
      </Card>

      {results && (
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardHeader className="pb-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <BookOpenCheck className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                <CardTitle className="text-sm font-bold text-[#102C54]">
                  Matched Policy Clauses ({results.hits.length})
                </CardTitle>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-textSecondary">
                <span>
                  Detected Query Language:{" "}
                  <strong className="text-[#102C54]">
                    {formatLanguageLabel(results.query_language)}
                  </strong>
                </span>
                <span>•</span>
                <span>Candidates scanned: {results.total_candidates}</span>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {results.hits.length === 0 ? (
              <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] p-6 text-center space-y-1">
                <p className="text-xs font-semibold text-[#102C54]">
                  No matching policy clauses found.
                </p>
                <p className="text-[11px] text-textSecondary">
                  Only published Active policies visible to your role are searchable. Try broader
                  English or Urdu keywords or clear type/language filters.
                </p>
              </div>
            ) : (
              <ul className="space-y-3">
                {results.hits.map((hit) => (
                  <li
                    key={hit.citation.chunk_id}
                    className="rounded-medium border border-appBorder bg-[#F8F9FD] p-4 space-y-2.5"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2 border-b border-appBorder pb-2.5">
                      <div className="space-y-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-bold text-[#102C54]">
                            {hit.citation.title}
                          </span>
                          <Badge variant="outline" className="text-[10px] font-mono">
                            v{hit.citation.version}
                          </Badge>
                          <Badge variant="secondary" className="text-[10px]">
                            {formatDocType(hit.citation.doc_type)}
                          </Badge>
                          <VisibilityBadge visibility={hit.citation.visibility} />
                          <Badge variant="outline" className="text-[10px] font-mono">
                            {formatLanguageLabel(hit.citation.language)}
                          </Badge>
                        </div>
                        <p className="text-[11px] font-medium text-[#2A5BDB]">
                          {hit.citation.section_path} • {hit.citation.page_range}
                        </p>
                      </div>

                      <div className="flex flex-wrap items-center gap-2 shrink-0">
                        <Badge variant="primary" className="text-[11px] font-mono">
                          Score: {(hit.score * 100).toFixed(0)}%
                        </Badge>
                        <Link
                          href={`/policies/${hit.citation.document_id}/preview`}
                          className="inline-flex items-center gap-1 rounded-small border border-appBorder bg-white px-2.5 py-1 text-[11px] font-semibold text-[#102C54] hover:border-[#2A5BDB] hover:text-[#2A5BDB] transition-colors"
                        >
                          Inspect Source
                          <ExternalLink className="h-3 w-3" aria-hidden="true" />
                        </Link>
                      </div>
                    </div>

                    {hit.has_injection_flag && (
                      <div className="inline-flex items-center gap-1.5 rounded-small border border-amber-300 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-900">
                        <ShieldAlert
                          className="h-3.5 w-3.5 text-amber-700 shrink-0"
                          aria-hidden="true"
                        />
                        Prompt-Injection Flagged (Untrusted Text)
                      </div>
                    )}

                    <p
                      dir="auto"
                      className="rounded-small bg-white border border-appBorder px-3 py-2.5 text-xs leading-relaxed text-[#102C54] whitespace-pre-wrap"
                    >
                      {hit.snippet || hit.text}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
