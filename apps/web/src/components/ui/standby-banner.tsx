"use client";

import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface StandbyBannerProps {
  title?: string;
  message: string;
  onRetry?: () => void;
  isRetrying?: boolean;
}

export function StandbyBanner({
  title = "FastAPI Backend in Standby Mode",
  message,
  onRetry,
  isRetrying = false,
}: StandbyBannerProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-medium border border-amber-200 bg-amber-50/90 px-4 py-3 text-xs text-amber-900 shadow-sm"
    >
      <div className="flex items-start sm:items-center gap-2.5">
        <AlertTriangle className="h-4 w-4 text-amber-600 flex-shrink-0 mt-0.5 sm:mt-0" />
        <div>
          <span className="font-bold text-[#102C54] mr-1.5">{title}:</span>
          <span>{message}</span>
        </div>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          disabled={isRetrying}
          className="inline-flex items-center justify-center gap-1.5 rounded-small border border-[#2A5BDB]/30 bg-white px-3 py-1.5 text-xs font-semibold text-[#2A5BDB] hover:bg-[#F8F9FD] transition-colors disabled:opacity-60 flex-shrink-0"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isRetrying ? "animate-spin" : ""}`} />
          {isRetrying ? "Retrying..." : "Retry"}
        </button>
      )}
    </div>
  );
}
