"use client";

import React, { useState } from "react";
import Link from "next/link";
import { BookOpen, ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import type { CitationItem } from "@agentic-hr/contracts";

interface CitationChipsProps {
  citations: CitationItem[];
}

export function CitationChips({ citations }: CitationChipsProps) {
  const [showSnippets, setShowSnippets] = useState(false);

  if (!citations || citations.length === 0) {
    return null;
  }

  return (
    <div className="mt-3 space-y-2 border-t border-appBorder/70 pt-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-[#102C54] inline-flex items-center gap-1">
          <BookOpen className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
          Verified Sources ({citations.length})
        </span>
        <button
          type="button"
          onClick={() => setShowSnippets((prev) => !prev)}
          className="text-[11px] font-medium text-[#2A5BDB] hover:underline inline-flex items-center gap-0.5"
        >
          {showSnippets ? (
            <>
              Hide snippets
              <ChevronUp className="h-3 w-3" aria-hidden="true" />
            </>
          ) : (
            <>
              Show snippets
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
            </>
          )}
        </button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {citations.map((cit, idx) => {
          const pageLabel = cit.page ?? 1;
          const pageQuery = encodeURIComponent(String(pageLabel));
          const chunkQuery = encodeURIComponent(cit.chunk_id);
          const href = `/policies/${cit.document_id}/preview?page=${pageQuery}&chunk=${chunkQuery}`;

          return (
            <Link
              key={`${cit.chunk_id}-${idx}`}
              href={href}
              title={cit.snippet}
              className="inline-flex items-center gap-1.5 rounded-full border border-[#2A5BDB]/30 bg-blue-50/70 px-2.5 py-1 text-[11px] font-medium text-[#102C54] hover:border-[#2A5BDB] hover:bg-blue-100/70 transition-colors"
            >
              <span>
                {cit.title} &middot; v{cit.version} &middot; p.{pageLabel}
              </span>
              <ExternalLink className="h-3 w-3 text-[#2A5BDB]" aria-hidden="true" />
            </Link>
          );
        })}
      </div>

      {showSnippets && (
        <div className="space-y-1.5 rounded-small border border-appBorder bg-[#F8F9FD] p-2.5">
          {citations.map((cit, idx) => (
            <div key={`snippet-${cit.chunk_id}-${idx}`} className="text-[11px] space-y-0.5">
              <p className="font-semibold text-[#102C54]">
                {cit.title} (v{cit.version}
                {cit.section ? ` · ${cit.section}` : ""})
              </p>
              <p dir="auto" className="text-textSecondary leading-relaxed">
                {cit.snippet}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
