"use client";

import React, { useEffect } from "react";
import Link from "next/link";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useToastStore } from "@/stores/toast-store";

const AUTO_DISMISS_MS = 4500;

export function ToastProvider() {
  const toasts = useToastStore((s) => s.toasts);
  const dismissToast = useToastStore((s) => s.dismissToast);

  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts.map((toast) =>
      window.setTimeout(() => {
        dismissToast(toast.id);
      }, AUTO_DISMISS_MS)
    );
    return () => {
      for (const timer of timers) {
        window.clearTimeout(timer);
      }
    };
  }, [toasts, dismissToast]);

  if (toasts.length === 0) return null;

  return (
    <div
      aria-live="polite"
      aria-label="Workspace notifications"
      className="fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2 pointer-events-none px-4 sm:px-0"
    >
      {toasts.map((toast) => {
        const isError = toast.variant === "error";
        const isSuccess = toast.variant === "success";
        return (
          <div
            key={toast.id}
            role={isError ? "alert" : "status"}
            className={`pointer-events-auto flex items-start gap-2.5 rounded-medium border px-3.5 py-3 shadow-lg text-xs transition-all ${
              isError
                ? "border-rose-300 bg-rose-50 text-rose-900"
                : isSuccess
                  ? "border-emerald-300 bg-white text-[#102C54]"
                  : "border-[#2A5BDB]/30 bg-white text-[#102C54]"
            }`}
          >
            {isError ? (
              <XCircle className="h-4 w-4 text-rose-600 shrink-0 mt-0.5" aria-hidden="true" />
            ) : isSuccess ? (
              <CheckCircle2
                className="h-4 w-4 text-emerald-600 shrink-0 mt-0.5"
                aria-hidden="true"
              />
            ) : (
              <Info className="h-4 w-4 text-[#2A5BDB] shrink-0 mt-0.5" aria-hidden="true" />
            )}
            <div className="min-w-0 flex-1">
              <p className="font-bold leading-snug">{toast.title}</p>
              {toast.description && (
                <p className="mt-0.5 text-[11px] opacity-85 break-words">{toast.description}</p>
              )}
              {toast.actionHref && toast.actionLabel && (
                <Link
                  href={toast.actionHref}
                  onClick={() => dismissToast(toast.id)}
                  className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-semibold text-[#2A5BDB] underline underline-offset-2 hover:text-[#102C54]"
                >
                  {toast.actionLabel}
                </Link>
              )}
            </div>
            <button
              type="button"
              onClick={() => dismissToast(toast.id)}
              aria-label="Dismiss notification"
              className="rounded p-0.5 opacity-70 hover:opacity-100"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
