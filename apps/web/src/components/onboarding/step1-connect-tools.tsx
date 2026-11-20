"use client";

import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Lock,
  Mail,
  RefreshCw,
  ToggleLeft,
  ToggleRight,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useConnectors,
  useReconnectConnector,
  useRefreshConnector,
  useToggleReadOnly,
} from "@/hooks/use-connectors";
import { useCurrentUser } from "@/hooks/use-current-user";
import { isOfflineError } from "@/lib/api-request";
import { useConnectorStore } from "@/stores/connector-store";

interface Step1ConnectToolsProps {
  isSavingStep: boolean;
  onSaveAndContinue: () => void;
}

export function Step1ConnectTools({ isSavingStep, onSaveAndContinue }: Step1ConnectToolsProps) {
  const { data: currentUser } = useCurrentUser();
  const { data: connectors, isLoading, error: connectorsError, refetch } = useConnectors();
  const toggleReadOnly = useToggleReadOnly();
  const refreshConnector = useRefreshConnector();
  const reconnectConnector = useReconnectConnector();

  const connector = useConnectorStore((s) => s.connector);
  const setConnector = useConnectorStore((s) => s.setConnector);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (connectors && connectors.length > 0) {
      const primary =
        connectors.find((c) => c.account_email === "agentic.hr.test.ubaid.owner@gmail.com") ||
        connectors[0];
      setConnector(primary);
    }
  }, [connectors, setConnector]);

  const userRole = currentUser?.role?.toLowerCase() || "owner";
  const canManage =
    userRole === "owner" || userRole === "manager" || currentUser?.is_hr_head === true;

  const isReadOnly = connector.is_read_only;
  const status = connector.status;
  const isNeedsReconnect = status === "needs_reconnect" || connector.jobs_paused;
  const liveConnectorHttpError =
    connectorsError && !isOfflineError(connectorsError) ? connectorsError.message : null;

  const handleToggleReadOnly = () => {
    if (!canManage) return;
    setActionError(null);
    setActionSuccess(null);
    toggleReadOnly.mutate(
      { credentialId: connector.id, isReadOnly: !isReadOnly },
      {
        onSuccess: (res) => {
          if (res?.data) {
            setConnector(res.data);
            setActionSuccess(
              res.data.is_read_only
                ? "Read-Only Mode enabled. Outbound writes are safely blocked."
                : "Read-Only Mode disabled. Outbound Gmail, Drive, and Calendar writes are active."
            );
          }
        },
        onError: (err) => {
          setActionError(err.message || "Failed to update Read-Only Mode.");
        },
      }
    );
  };

  const handleRefreshToken = () => {
    if (!canManage) return;
    setActionError(null);
    setActionSuccess(null);
    refreshConnector.mutate(connector.id, {
      onSuccess: (res) => {
        if (res?.data) {
          setConnector(res.data);
          setActionSuccess("OAuth access token refreshed (+1h expiry).");
        }
      },
      onError: (err) => {
        setActionError(err.message || "Failed to refresh OAuth token.");
      },
    });
  };

  const handleReconnect = () => {
    if (!canManage) return;
    setActionError(null);
    setActionSuccess(null);
    reconnectConnector.mutate(connector.id, {
      onSuccess: (res) => {
        if (res?.data) {
          setConnector(res.data);
          setActionSuccess("Google Workspace connector reconnected.");
        }
      },
      onError: (err) => {
        setActionError(err.message || "Failed to reconnect Google Workspace.");
      },
    });
  };

  return (
    <div className="space-y-6">
      {(actionError || liveConnectorHttpError) && (
        <div
          role="alert"
          className="flex items-center justify-between rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{actionError || liveConnectorHttpError}</span>
          </div>
          <button
            type="button"
            onClick={() => {
              setActionError(null);
              refetch();
            }}
            className="font-semibold text-rose-800 hover:underline"
          >
            Retry
          </button>
        </div>
      )}

      {actionSuccess && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 rounded-medium border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-medium text-emerald-800"
        >
          <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
          <span>{actionSuccess}</span>
        </div>
      )}

      {isNeedsReconnect && (
        <div
          role="alert"
          className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-medium border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" aria-hidden="true" />
            <span>
              <strong>Authentication Expired:</strong> Automated emails and sync jobs are paused
              until Google Workspace is reconnected.
            </span>
          </div>
          {canManage && (
            <button
              type="button"
              onClick={handleReconnect}
              disabled={reconnectConnector.isPending}
              className="inline-flex items-center gap-1.5 rounded bg-[#2A5BDB] px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3 w-3 ${reconnectConnector.isPending ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              Reconnect OAuth
            </button>
          )}
        </div>
      )}

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-medium bg-[#2A5BDB]/10">
                <Mail className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
              </div>
              <div>
                <CardTitle className="text-base text-[#102C54]">
                  Step 1 — Google Workspace Connector (ADR 006)
                </CardTitle>
                <CardDescription className="text-xs text-textSecondary">
                  Verify least-privilege Google OAuth scopes for outbound emails, Drive vaults, and
                  Sheets sync.
                </CardDescription>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {isReadOnly && (
                <Badge
                  variant="outline"
                  className="border-blue-400 bg-blue-50 text-[#2A5BDB] font-medium"
                >
                  Read-Only Active
                </Badge>
              )}
              {status === "connected" && <Badge variant="success">Connected</Badge>}
              {status === "needs_reconnect" && <Badge variant="warning">Needs Reconnect</Badge>}
              {status === "revoked" && (
                <Badge variant="outline" className="border-rose-300 bg-rose-50 text-rose-700">
                  Revoked
                </Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 text-xs text-textSecondary">
          {isLoading ? (
            <div className="space-y-3 animate-pulse" aria-busy="true">
              <div className="h-10 rounded-small bg-slate-100" />
              <div className="h-16 rounded-medium bg-slate-100" />
            </div>
          ) : (
            <>
              <div className="border-b border-appBorder pb-3">
                <span className="block text-[11px] text-textMuted">Connected Account Email</span>
                <span className="font-mono font-semibold text-textPrimary">
                  {connector.account_email}
                </span>
              </div>

              {/* Interactive Read-Only Switch */}
              <div className="rounded-medium border border-appBorder bg-[#F8F9FD] p-3.5">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <span className="block font-semibold text-textPrimary">
                      Client-Enforced Read-Only Mode
                    </span>
                    <span className="text-[11px] text-textMuted">
                      When enabled, outbound invitations and Gmail alerts are blocked (HTTP 403).
                    </span>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={isReadOnly}
                    aria-label="Toggle Read-Only Mode"
                    onClick={handleToggleReadOnly}
                    disabled={!canManage || toggleReadOnly.isPending}
                    className={`focus:outline-none transition-colors ${
                      !canManage ? "cursor-not-allowed opacity-60" : "cursor-pointer"
                    }`}
                  >
                    {isReadOnly ? (
                      <ToggleRight className="h-7 w-7 text-[#2A5BDB]" aria-hidden="true" />
                    ) : (
                      <ToggleLeft className="h-7 w-7 text-slate-400" aria-hidden="true" />
                    )}
                  </button>
                </div>
              </div>

              {/* Approved Scopes */}
              <div className="border-b border-appBorder pb-3">
                <span className="mb-1.5 block text-[11px] text-textMuted">
                  ADR-006 Approved Least-Privilege Scopes
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {["gmail.send", "drive.file", "calendar.events"].map((scope) => (
                    <span
                      key={scope}
                      className="inline-flex items-center gap-1 rounded border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-mono text-[11px] text-emerald-800"
                    >
                      <CheckCircle2 className="h-3 w-3 text-emerald-600" aria-hidden="true" />
                      {scope}
                    </span>
                  ))}
                </div>
              </div>

              {/* Prohibited Scopes Notice */}
              <div className="flex flex-wrap items-center justify-between gap-2 pb-1">
                <div className="flex items-center gap-1.5 font-mono text-[11px] text-rose-700">
                  <XCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>Prohibited: gmail.readonly, gmail.modify, mail.google.com</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={handleRefreshToken}
                    disabled={!canManage || refreshConnector.isPending}
                    className="inline-flex items-center gap-1.5 rounded border border-appBorder bg-[#F8F9FD] px-3 py-1.5 text-xs font-medium text-textPrimary hover:bg-slate-100 disabled:opacity-50"
                  >
                    <RefreshCw
                      className={`h-3.5 w-3.5 ${refreshConnector.isPending ? "animate-spin" : ""}`}
                      aria-hidden="true"
                    />
                    {refreshConnector.isPending ? "Refreshing..." : "Refresh Token"}
                  </button>
                </div>
              </div>

              {!canManage && (
                <p className="flex items-center gap-1 text-[11px] italic text-textMuted">
                  <Lock className="h-3 w-3" aria-hidden="true" />
                  Owner, Manager, or HR Head role required to modify connector settings.
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center justify-end pt-2">
        <Button
          type="button"
          onClick={onSaveAndContinue}
          disabled={isSavingStep || !canManage}
          className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-2"
        >
          {isSavingStep ? "Saving Step 1..." : "Save & Continue to Policies"}
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
