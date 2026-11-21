"use client";

import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#F8F9FD] text-[#102C54] flex items-center justify-center p-6 font-sans">
        <div className="max-w-md w-full rounded-xl border border-slate-200 bg-white p-8 shadow-lg space-y-5 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-rose-50 text-rose-600">
            <AlertTriangle className="h-6 w-6" aria-hidden="true" />
          </div>
          <div className="space-y-2">
            <h1 className="text-xl font-bold text-[#102C54]">Unexpected Platform Error</h1>
            <p className="text-xs text-slate-600 leading-relaxed">
              {error?.message || "An unexpected error interrupted the Agentic HR Core workspace."}
            </p>
            {error?.digest && (
              <p className="text-[10px] font-mono text-slate-400">Reference ID: {error.digest}</p>
            )}
          </div>
          <button
            type="button"
            onClick={() => reset()}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-[#2A5BDB] px-4 py-2.5 text-xs font-semibold text-white hover:bg-[#102C54] transition-colors"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            Reload Workspace
          </button>
        </div>
      </body>
    </html>
  );
}
