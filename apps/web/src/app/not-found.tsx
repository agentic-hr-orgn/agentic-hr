import React from "react";
import Link from "next/link";
import { ArrowLeft, Compass } from "lucide-react";

export default function NotFound() {
  return (
    <div className="min-h-screen bg-[#F8F9FD] flex items-center justify-center p-6">
      <div className="max-w-md w-full rounded-xl border border-appBorder bg-white p-8 shadow-card text-center space-y-5">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[#2A5BDB]/10 text-[#2A5BDB]">
          <Compass className="h-6 w-6" aria-hidden="true" />
        </div>
        <div className="space-y-2">
          <p className="text-xs font-bold uppercase tracking-wider text-[#2A5BDB]">404 Not Found</p>
          <h1 className="text-2xl font-bold text-[#102C54]">Workspace Page Not Found</h1>
          <p className="text-xs text-textSecondary leading-relaxed">
            The route you requested does not exist in Agentic HR Core or has been moved.
          </p>
        </div>
        <div className="flex items-center justify-center gap-3 pt-1">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-medium bg-[#2A5BDB] px-4 py-2.5 text-xs font-semibold text-white hover:bg-[#102C54] transition-colors"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            Back to Dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
