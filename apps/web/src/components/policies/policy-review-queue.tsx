"use client";

import React from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, ExternalLink, RefreshCw, XCircle } from "lucide-react";
import type { PolicyReviewQueueItem } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  IngestionStatusBadge,
  LifecycleStatusBadge,
  formatDocType,
} from "@/components/policies/policy-status-badges";
import { isOfflineError } from "@/lib/api-request";

interface PolicyReviewQueueProps {
  items: PolicyReviewQueueItem[];
  isLoading: boolean;
  error: Error | null;
  isOffline: boolean;
  onReingest: (policyId: string, title: string) => Promise<void>;
  reingestingId: string | null;
}

export function PolicyReviewQueue({
  items,
  isLoading,
  error,
  isOffline,
  onReingest,
  reingestingId,
}: PolicyReviewQueueProps) {
  const liveError = error && !isOfflineError(error) ? error.message : null;

  return (
    <Card className="border border-appBorder bg-white shadow-sm">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />
            <CardTitle className="text-base text-[#102C54]">
              OCR &amp; Ingestion Review Queue ({items.length})
            </CardTitle>
          </div>
          <Badge variant={items.length > 0 ? "warning" : "success"}>
            {items.length > 0 ? "Attention Needed" : "All Clean"}
          </Badge>
        </div>
        <CardDescription className="text-xs">
          Policies with low-confidence / scanned pages (`needs_ocr`) or failed extraction must be
          reviewed and indexed before they can be submitted for publishing.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {liveError && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
          >
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{liveError}</span>
          </div>
        )}

        {isLoading ? (
          <div className="space-y-2 animate-pulse" aria-busy="true">
            <div className="h-16 rounded-small bg-slate-100" />
            <div className="h-16 rounded-small bg-slate-100" />
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center space-y-1.5">
            <CheckCircle2 className="h-7 w-7 text-emerald-600 mx-auto" aria-hidden="true" />
            <p className="text-xs font-semibold text-[#102C54]">
              No low-confidence OCR or failed ingestion items in the queue.
            </p>
            <p className="text-[11px] text-textSecondary">
              All non-archived policy documents passed extraction quality checks.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => {
              const doc = item.document;
              const lowPages = Array.isArray(doc.low_confidence_pages)
                ? doc.low_confidence_pages
                : [];
              const isBusy = reingestingId === doc.id;

              return (
                <li
                  key={doc.id}
                  className="rounded-medium border border-amber-200 bg-[#F8F9FD] p-4 space-y-3"
                >
                  <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                    <div className="space-y-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-bold text-[#102C54]">{doc.title}</span>
                        <Badge variant="outline" className="text-[10px] font-mono">
                          v{doc.version ?? 1}
                        </Badge>
                        <Badge variant="secondary" className="text-[10px]">
                          {formatDocType(doc.doc_type)}
                        </Badge>
                        <IngestionStatusBadge
                          status={doc.ingestion_status}
                          progressPct={doc.progress_pct}
                          progressStage={doc.progress_stage}
                        />
                        <LifecycleStatusBadge status={doc.lifecycle_status} />
                      </div>
                      <p className="text-[11px] text-textSecondary">
                        {doc.file_name} • {item.low_confidence_count} low-confidence page(s)
                      </p>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 shrink-0">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={isBusy || isOffline}
                        onClick={() => void onReingest(doc.id, doc.title)}
                        className="text-xs inline-flex items-center gap-1"
                      >
                        <RefreshCw
                          className={`h-3.5 w-3.5 ${isBusy ? "animate-spin" : ""}`}
                          aria-hidden="true"
                        />
                        {isBusy ? "Re-ingesting..." : "Re-ingest"}
                      </Button>
                      <Link
                        href={`/policies/${doc.id}/preview`}
                        className="inline-flex items-center gap-1.5 rounded-small bg-[#2A5BDB] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#102C54] transition-colors"
                      >
                        Inspect &amp; Fix
                        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                      </Link>
                    </div>
                  </div>

                  {lowPages.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[11px] font-semibold text-amber-900">
                        Affected Pages:
                      </span>
                      {lowPages.map((entry, idx) => {
                        const pageNum =
                          typeof entry.page_number === "number" ? entry.page_number : idx + 1;
                        const quality =
                          typeof entry.quality_score === "number"
                            ? `${Math.round(entry.quality_score * 100)}% quality`
                            : "Needs OCR";
                        return (
                          <span
                            key={`${doc.id}-page-${pageNum}`}
                            className="inline-flex items-center rounded-small border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-900"
                          >
                            Page {pageNum} ({quality})
                          </span>
                        );
                      })}
                    </div>
                  )}

                  {doc.error_message && (
                    <p className="rounded-small border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] font-medium text-rose-900">
                      Error: {doc.error_message}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
