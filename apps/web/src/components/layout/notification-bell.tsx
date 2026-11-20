"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, Bell, CheckCheck, Info, RefreshCw, ShieldAlert, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
  useTriggerExpiryScan,
} from "@/hooks/use-notifications";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";

export function NotificationBell() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dialogId = useId();
  const titleId = useId();

  const { isStandby } = useBackendStatus();
  const { data: currentUser } = useCurrentUser();
  const { data, isLoading, error } = useNotifications({ limit: 20 });
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  const triggerScan = useTriggerExpiryScan();

  const [isOpen, setIsOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const isOffline = isStandby || isOfflineError(error);
  const unreadCount = !isOffline ? (data?.unread_count ?? 0) : 0;
  const items = data?.items ?? [];

  const userRole = currentUser?.role?.toLowerCase() || "employee";
  const canTriggerScan = userRole === "owner" || currentUser?.is_hr_head === true;

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsOpen(false);
      }
    };
    const onPointerDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [isOpen]);

  const handleMarkItemRead = async (notificationId: string) => {
    setActionError(null);
    try {
      await markRead.mutateAsync(notificationId);
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to mark notification read.");
    }
  };

  const handleMarkAllRead = async () => {
    setActionError(null);
    setActionSuccess(null);
    try {
      const res = await markAllRead.mutateAsync();
      setActionSuccess(`Marked ${res.updated} notification(s) as read.`);
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to mark all notifications read.");
    }
  };

  const handleTriggerScan = async () => {
    setActionError(null);
    setActionSuccess(null);
    try {
      const summary = await triggerScan.mutateAsync(undefined);
      setActionSuccess(
        `Scan complete: ${summary.alerts_created} new alert(s), ${summary.emails_sent} email(s) sent.`
      );
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to run expiry scan.");
    }
  };

  const renderSeverityIcon = (severity: string) => {
    if (severity === "critical") {
      return <ShieldAlert className="h-4 w-4 text-rose-600 shrink-0 mt-0.5" aria-hidden="true" />;
    }
    if (severity === "warning") {
      return (
        <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" aria-hidden="true" />
      );
    }
    return <Info className="h-4 w-4 text-[#2A5BDB] shrink-0 mt-0.5" aria-hidden="true" />;
  };

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => {
          setActionError(null);
          setActionSuccess(null);
          setIsOpen((prev) => !prev);
        }}
        aria-label={unreadCount > 0 ? `Notifications (${unreadCount} unread)` : "Notifications"}
        aria-expanded={isOpen}
        aria-haspopup="dialog"
        aria-controls={isOpen ? dialogId : undefined}
        className="relative flex h-8 w-8 items-center justify-center rounded-small border border-appBorder bg-[#F8F9FD] text-[#102C54] hover:bg-slate-100 transition-colors"
      >
        <Bell className="h-4 w-4" aria-hidden="true" />
        {unreadCount > 0 && (
          <span
            data-testid="notification-unread-badge"
            className="absolute -top-1.5 -right-1.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-rose-600 px-1 py-0.5 text-[10px] font-bold leading-none text-white"
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div
          id={dialogId}
          role="dialog"
          aria-labelledby={titleId}
          className="absolute right-0 mt-2 z-50 w-80 sm:w-96 rounded-medium border border-appBorder bg-white shadow-xl"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-appBorder bg-[#F8F9FD] px-4 py-3">
            <div className="flex items-center gap-2">
              <h3 id={titleId} className="text-xs font-bold text-[#102C54]">
                Notifications
              </h3>
              {unreadCount > 0 && (
                <Badge variant="primary" className="text-[10px]">
                  {unreadCount} unread
                </Badge>
              )}
            </div>
            <button
              type="button"
              onClick={() => setIsOpen(false)}
              aria-label="Close notifications panel"
              className="rounded p-1 text-textSecondary hover:bg-slate-200/70"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>

          {/* Controls */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-appBorder px-4 py-2 text-[11px]">
            <button
              type="button"
              onClick={() => void handleMarkAllRead()}
              disabled={unreadCount === 0 || markAllRead.isPending}
              className="inline-flex items-center gap-1 font-semibold text-[#2A5BDB] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
            >
              <CheckCheck className="h-3.5 w-3.5" aria-hidden="true" />
              Mark all read
            </button>

            {canTriggerScan && (
              <button
                type="button"
                onClick={() => void handleTriggerScan()}
                disabled={triggerScan.isPending}
                className="inline-flex items-center gap-1 rounded border border-appBorder bg-[#F8F9FD] px-2 py-1 font-semibold text-[#102C54] hover:bg-slate-100 disabled:opacity-50"
              >
                <RefreshCw
                  className={`h-3 w-3 ${triggerScan.isPending ? "animate-spin" : ""}`}
                  aria-hidden="true"
                />
                {triggerScan.isPending ? "Scanning..." : "Run Expiry Scan"}
              </button>
            )}
          </div>

          {actionError && (
            <div
              role="alert"
              className="border-b border-rose-200 bg-rose-50 px-4 py-2 text-[11px] font-medium text-rose-800"
            >
              {actionError}
            </div>
          )}

          {actionSuccess && (
            <div
              role="status"
              aria-live="polite"
              className="border-b border-emerald-200 bg-emerald-50 px-4 py-2 text-[11px] font-medium text-emerald-800"
            >
              {actionSuccess}
            </div>
          )}

          {/* List Body */}
          <div className="max-h-80 overflow-y-auto overscroll-contain divide-y divide-appBorder">
            {isOffline ? (
              <div className="p-5 text-center text-xs text-textSecondary">
                Notifications unavailable while FastAPI backend is in Standby mode.
              </div>
            ) : error ? (
              <div role="alert" className="p-4 text-xs text-rose-800">
                {error.message}
              </div>
            ) : isLoading ? (
              <div className="p-4 space-y-2 animate-pulse" aria-busy="true">
                <div className="h-12 rounded bg-slate-100" />
                <div className="h-12 rounded bg-slate-100" />
              </div>
            ) : items.length === 0 ? (
              <div className="p-6 text-center text-xs text-textSecondary">
                You are all caught up — no notifications yet.
              </div>
            ) : (
              items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => void handleMarkItemRead(item.id)}
                  className={`flex w-full items-start gap-2.5 px-4 py-3 text-left text-xs transition-colors hover:bg-[#F8F9FD] ${
                    !item.is_read ? "bg-[#2A5BDB]/5" : "bg-white"
                  }`}
                >
                  {renderSeverityIcon(item.severity)}
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span
                        className={`truncate font-semibold ${
                          item.is_read ? "text-textSecondary" : "text-[#102C54]"
                        }`}
                      >
                        {item.title}
                      </span>
                      {!item.is_read && (
                        <span
                          className="h-2 w-2 shrink-0 rounded-full bg-[#2A5BDB]"
                          aria-label="Unread"
                        />
                      )}
                    </div>
                    <p className="text-[11px] text-textSecondary line-clamp-2">{item.message}</p>
                    <div className="flex items-center justify-between gap-2 text-[10px] text-textMuted">
                      <Badge
                        variant={
                          item.severity === "critical"
                            ? "outline"
                            : item.severity === "warning"
                              ? "warning"
                              : "primary"
                        }
                        className={`text-[9px] px-1.5 py-0 ${
                          item.severity === "critical"
                            ? "border-rose-300 bg-rose-50 text-rose-700"
                            : ""
                        }`}
                      >
                        {item.severity}
                      </Badge>
                      <span>{formatPKT(item.created_at)}</span>
                    </div>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
