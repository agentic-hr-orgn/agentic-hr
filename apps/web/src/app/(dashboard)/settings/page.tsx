"use client";

import React, { useState, useEffect, useId, useRef } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  Mail,
  ArrowLeft,
  Cpu,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  ToggleLeft,
  ToggleRight,
  PowerOff,
  Lock,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { LeaveAttendanceConfigPanel } from "@/components/leave/leave-attendance-config-panel";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import {
  useConnectors,
  useToggleReadOnly,
  useRefreshConnector,
  useRevokeConnector,
  useReconnectConnector,
  useGoogleAuthorize,
} from "@/hooks/use-connectors";
import { useConnectorStore } from "@/stores/connector-store";

export default function SettingsPage() {
  const { data: currentUser } = useCurrentUser();
  const {
    isStandby,
    useFakeIntegrations,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();
  const { data: connectors, isLoading, refetch: refetchConnectors } = useConnectors();
  const toggleReadOnly = useToggleReadOnly();
  const refreshConnector = useRefreshConnector();
  const revokeConnector = useRevokeConnector();
  const reconnectConnector = useReconnectConnector();
  const googleAuthorize = useGoogleAuthorize();

  // Zustand persistent client store
  const connector = useConnectorStore((s) => s.connector);
  const bannerMessage = useConnectorStore((s) => s.bannerMessage);
  const errorMessage = useConnectorStore((s) => s.errorMessage);
  const setBannerMessage = useConnectorStore((s) => s.setBannerMessage);
  const setErrorMessage = useConnectorStore((s) => s.setErrorMessage);
  const setConnector = useConnectorStore((s) => s.setConnector);

  const [isHydrated, setIsHydrated] = useState(false);
  const [activeTab, setActiveTab] = useState<"connectors" | "leave-rules">("connectors");
  const tabConnId = useId();
  const tabLeaveId = useId();
  const tabRefs = useRef<Partial<Record<"connectors" | "leave-rules", HTMLButtonElement | null>>>(
    {}
  );

  useEffect(() => {
    setIsHydrated(true);
  }, []);

  useEffect(() => {
    if (connectors && connectors.length > 0) {
      const primary =
        connectors.find((c) => c.account_email === "agentic.hr.test.ubaid.owner@gmail.com") ||
        connectors[0];
      setConnector(primary);
    }
  }, [connectors, setConnector]);

  // Role permissions: Only Owner, Manager, or HR Head can mutate connectors
  const userRole = currentUser?.role?.toLowerCase() || "owner";
  const canManage =
    userRole === "owner" || userRole === "manager" || currentUser?.is_hr_head === true;
  const canManageLeaveRules = userRole === "owner" || currentUser?.is_hr_head === true;

  const settingsTabs = [
    { id: "connectors" as const, domId: tabConnId, label: "Platform & Connectors" },
    { id: "leave-rules" as const, domId: tabLeaveId, label: "Leave & Attendance Rules" },
  ];

  const handleTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, idx: number) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const next = settingsTabs[(idx + 1) % settingsTabs.length];
      setActiveTab(next.id);
      tabRefs.current[next.id]?.focus();
    }
  };

  const activeConnector = connector;
  const isReadOnly = activeConnector.is_read_only;
  const status = activeConnector.status;
  const isNeedsReconnect = status === "needs_reconnect" || activeConnector.jobs_paused;

  const handleToggleReadOnly = () => {
    if (!canManage) return;
    setErrorMessage(null);
    toggleReadOnly.mutate(
      {
        credentialId: activeConnector.id,
        isReadOnly: !isReadOnly,
      },
      {
        onSuccess: (res) => {
          if (res?.data) setConnector(res.data);
        },
        onError: (err) => {
          setBannerMessage(null);
          setErrorMessage(err.message || "Failed to update Read-Only Mode.");
        },
      }
    );
  };

  const handleRefreshToken = () => {
    if (!canManage) return;
    setErrorMessage(null);
    refreshConnector.mutate(activeConnector.id, {
      onSuccess: (res) => {
        if (res?.data) setConnector(res.data);
      },
      onError: (err) => {
        setBannerMessage(null);
        setErrorMessage(err.message || "Failed to refresh OAuth token.");
      },
    });
  };

  const handleRevoke = () => {
    if (!canManage) return;
    if (
      confirm(
        "Are you sure you want to disconnect this Google Workspace connector? Outbound automations will be paused."
      )
    ) {
      setErrorMessage(null);
      revokeConnector.mutate(activeConnector.id);
    }
  };

  const handleReconnect = () => {
    if (!canManage) return;
    setErrorMessage(null);
    reconnectConnector.mutate(activeConnector.id, {
      onSuccess: (res) => {
        if (res?.data) setConnector(res.data);
      },
      onError: (err) => {
        setBannerMessage(null);
        setErrorMessage(err.message || "Failed to reconnect Google Workspace.");
      },
    });
  };

  const handleRetryBackend = () => {
    refetchHealth();
    refetchConnectors();
  };

  return (
    <div className="space-y-6" data-hydrated={isHydrated}>
      {/* Top Navigation & Breadcrumbs */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-appBorder pb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              href="/"
              className="text-xs text-textSecondary hover:text-primary flex items-center gap-1 font-medium transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Dashboard
            </Link>
            <span className="text-textMuted text-xs">/</span>
            <span className="text-xs text-textPrimary font-semibold">Settings</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-textPrimary">
            Platform Settings & Connectors
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Multi-tenant context isolation, Google Workspace least-privilege connectors, and token
            vault.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="primary">{currentUser?.tenant_name || "Acme Tech Solutions"}</Badge>
          <Badge variant={useFakeIntegrations ? "warning" : "success"}>
            {useFakeIntegrations ? "Fake Mode (ADR 006)" : "Live Mode (ADR 006)"}
          </Badge>
        </div>
      </div>

      {/* Standby Banner when FastAPI is offline */}
      {isStandby && (
        <StandbyBanner
          message="Connector actions are running in local simulation mode — changes are not persisted to PostgreSQL until FastAPI is online."
          onRetry={handleRetryBackend}
          isRetrying={isHealthFetching}
        />
      )}

      {/* Error Banner (Server HTTP 4xx/5xx failures — never fabricated as success) */}
      {errorMessage && (
        <div
          role="alert"
          className="p-3 bg-rose-50 border border-rose-200 rounded-medium text-rose-800 text-xs flex items-center justify-between shadow-sm"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 flex-shrink-0" />
            <span className="font-medium">{errorMessage}</span>
          </div>
          <button
            type="button"
            onClick={() => setErrorMessage(null)}
            className="text-rose-700 hover:text-rose-900 font-bold px-1"
            aria-label="Dismiss error"
          >
            &times;
          </button>
        </div>
      )}

      {/* Dynamic Status / Notification Banner */}
      {bannerMessage && (
        <div
          role="status"
          aria-live="polite"
          className="p-3 bg-emerald-50 border border-emerald-200 rounded-medium text-emerald-800 text-xs flex items-center justify-between shadow-sm"
        >
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-600 flex-shrink-0" />
            <span className="font-medium">{bannerMessage}</span>
          </div>
          <button
            type="button"
            onClick={() => setBannerMessage(null)}
            className="text-emerald-700 hover:text-emerald-900 font-bold px-1"
            aria-label="Dismiss banner"
          >
            &times;
          </button>
        </div>
      )}

      {/* Urgent Warning Alert Banner for Reconnect */}
      {isNeedsReconnect && (
        <div
          role="alert"
          className="p-4 bg-amber-50 border border-amber-200 rounded-medium flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-amber-900 text-xs shadow-sm"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-600 flex-shrink-0" />
            <span>
              <strong>Authentication Expired (401 / invalid_grant):</strong> Automated outbound
              email jobs are paused. Please re-authorize the Google Workspace connector.
            </span>
          </div>
          {canManage && (
            <button
              type="button"
              onClick={handleReconnect}
              disabled={googleAuthorize.isPending || reconnectConnector.isPending}
              className="px-3 py-1.5 bg-[#2A5BDB] text-white rounded text-xs font-semibold hover:bg-blue-700 transition flex items-center gap-1.5 flex-shrink-0"
            >
              <RefreshCw
                className={`h-3 w-3 ${googleAuthorize.isPending || reconnectConnector.isPending ? "animate-spin" : ""}`}
              />
              Reconnect OAuth
            </button>
          )}
        </div>
      )}

      <div className="border-b border-appBorder bg-white rounded-t-medium px-2">
        <div role="tablist" aria-label="Settings sections" className="flex gap-1 overflow-x-auto">
          {settingsTabs.map((tab, idx) => {
            const selected = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                ref={(el) => {
                  tabRefs.current[tab.id] = el;
                }}
                id={tab.domId}
                type="button"
                role="tab"
                aria-selected={selected}
                tabIndex={selected ? 0 : -1}
                onClick={() => setActiveTab(tab.id)}
                onKeyDown={(e) => handleTabKeyDown(e, idx)}
                className={`px-4 py-3 text-xs border-b-[3px] whitespace-nowrap transition-colors ${
                  selected
                    ? "border-[#2A5BDB] text-[#102C54] font-bold"
                    : "border-transparent text-textSecondary font-medium hover:text-[#102C54]"
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      {activeTab === "leave-rules" ? (
        <div role="tabpanel" aria-labelledby={tabLeaveId}>
          <LeaveAttendanceConfigPanel
            mode="settings"
            canManage={canManageLeaveRules}
            isOffline={isStandby}
          />
        </div>
      ) : (
        <div
          role="tabpanel"
          aria-labelledby={tabConnId}
          className="grid grid-cols-1 md:grid-cols-2 gap-6"
        >
          {/* Google Workspace Integration & Read-Only Mode Switch */}
          <Card className="border border-appBorder shadow-sm bg-white">
            <CardHeader>
              <div className="flex items-center justify-between mb-2">
                <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                  <Mail className="h-5 w-5 text-[#2A5BDB]" />
                </div>
                <div className="flex items-center gap-2">
                  {isReadOnly && (
                    <Badge
                      variant="outline"
                      className="border-blue-400 text-[#2A5BDB] bg-blue-50 font-medium"
                    >
                      Read-Only Active
                    </Badge>
                  )}
                  {status === "connected" && (
                    <Badge
                      variant="success"
                      className="bg-emerald-50 text-emerald-700 border-emerald-200 font-medium"
                    >
                      Connected
                    </Badge>
                  )}
                  {status === "needs_reconnect" && (
                    <Badge
                      variant="warning"
                      className="bg-amber-50 text-amber-800 border-amber-200 font-medium"
                    >
                      Needs Reconnect
                    </Badge>
                  )}
                  {status === "revoked" && (
                    <Badge
                      variant="outline"
                      className="border-red-400 text-red-700 bg-red-50 font-medium"
                    >
                      Revoked
                    </Badge>
                  )}
                  {status === "expired" && (
                    <Badge
                      variant="warning"
                      className="bg-amber-50 text-amber-700 border-amber-200 font-medium"
                    >
                      Expired
                    </Badge>
                  )}
                </div>
              </div>
              <CardTitle className="text-base text-[#102C54]">
                Google Workspace Connector (ADR 006)
              </CardTitle>
              <CardDescription className="text-xs text-textSecondary">
                Least-privilege OAuth credentials stored in AES-256-GCM encrypted vault.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-xs text-textSecondary space-y-4">
              {isLoading ? (
                <div className="space-y-3 animate-pulse" aria-busy="true">
                  <div className="h-10 rounded-small bg-slate-100" />
                  <div className="h-16 rounded-medium bg-slate-100" />
                  <div className="h-10 rounded-small bg-slate-100" />
                </div>
              ) : (
                <>
                  {/* Account & Provider */}
                  <div className="border-b border-appBorder pb-3">
                    <span className="text-textMuted block text-[11px]">
                      Connected Account Email
                    </span>
                    <span className="font-semibold text-textPrimary font-mono">
                      {activeConnector.account_email}
                    </span>
                  </div>

                  {/* Read-Only Switch */}
                  <div className="p-3 bg-[#F8F9FD] rounded-medium border border-appBorder">
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="font-semibold text-textPrimary block">
                          Client-Enforced Read-Only Mode
                        </span>
                        <span className="text-[11px] text-textMuted">
                          Blocks all outbound write operations (send email) with HTTP 403.
                        </span>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        data-hydrated={isHydrated}
                        aria-checked={isReadOnly}
                        aria-label="Toggle Read-Only Mode"
                        onClick={handleToggleReadOnly}
                        disabled={!canManage || toggleReadOnly.isPending}
                        className={`focus:outline-none transition-colors ${
                          !canManage ? "cursor-not-allowed opacity-60" : "cursor-pointer"
                        }`}
                        title={
                          canManage ? "Toggle Read-Only Mode" : "Owner or Manager role required"
                        }
                      >
                        {isReadOnly ? (
                          <ToggleRight className="h-7 w-7 text-[#2A5BDB]" />
                        ) : (
                          <ToggleLeft className="h-7 w-7 text-gray-400" />
                        )}
                      </button>
                    </div>
                    <div className="mt-2 pt-2 border-t border-appBorder/60">
                      {isReadOnly ? (
                        <span className="text-[11px] text-[#2A5BDB] font-semibold flex items-center gap-1">
                          <CheckCircle2 className="h-3.5 w-3.5 text-[#2A5BDB]" />
                          Read-Only Mode is active. All outbound write operations are blocked.
                        </span>
                      ) : (
                        <span className="text-[11px] text-emerald-600 font-semibold flex items-center gap-1">
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                          Write operations enabled (outbound send active).
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Scopes Whitelist */}
                  <div className="border-b border-appBorder pb-3">
                    <span className="text-textMuted block text-[11px] mb-1.5">
                      Permitted Least-Privilege Scopes
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      <span className="px-2 py-0.5 rounded bg-emerald-50 border border-emerald-200 text-emerald-800 text-[11px] font-mono flex items-center gap-1">
                        <CheckCircle2 className="h-3 w-3 text-emerald-600" /> gmail.send
                      </span>
                      <span className="px-2 py-0.5 rounded bg-emerald-50 border border-emerald-200 text-emerald-800 text-[11px] font-mono flex items-center gap-1">
                        <CheckCircle2 className="h-3 w-3 text-emerald-600" /> drive.file
                      </span>
                      <span className="px-2 py-0.5 rounded bg-emerald-50 border border-emerald-200 text-emerald-800 text-[11px] font-mono flex items-center gap-1">
                        <CheckCircle2 className="h-3 w-3 text-emerald-600" /> calendar.events
                      </span>
                    </div>
                  </div>

                  {/* Prohibited Scopes Notice */}
                  <div className="border-b border-appBorder pb-3">
                    <span className="text-textMuted block text-[11px] mb-1">
                      Strictly Prohibited Inbox Scopes
                    </span>
                    <div className="flex items-center gap-1.5 text-red-600 font-mono text-[11px]">
                      <XCircle className="h-3.5 w-3.5 flex-shrink-0" />
                      <span>gmail.readonly, gmail.modify, mail.google.com</span>
                    </div>
                    <span className="text-[11px] text-textMuted block mt-1">
                      Bounce handling is executed synchronously via send error capture & webhooks,
                      never inbox reading.
                    </span>
                  </div>

                  {/* Role-gated Actions */}
                  <div className="pt-1 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={handleRefreshToken}
                      disabled={!canManage || refreshConnector.isPending}
                      className="px-3 py-1.5 bg-[#F8F9FD] border border-appBorder text-textPrimary hover:bg-slate-100 rounded text-xs font-medium transition flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Refresh OAuth access token using stored encrypted refresh token"
                    >
                      <RefreshCw
                        className={`h-3.5 w-3.5 text-textSecondary ${refreshConnector.isPending ? "animate-spin" : ""}`}
                      />
                      {refreshConnector.isPending ? "Refreshing..." : "Refresh Token"}
                    </button>

                    <button
                      type="button"
                      onClick={handleReconnect}
                      disabled={!canManage || reconnectConnector.isPending}
                      className="px-3 py-1.5 bg-[#2A5BDB] hover:bg-blue-700 text-white rounded text-xs font-semibold transition flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <RefreshCw
                        className={`h-3.5 w-3.5 ${reconnectConnector.isPending ? "animate-spin" : ""}`}
                      />
                      {reconnectConnector.isPending ? "Reconnecting..." : "Reconnect OAuth"}
                    </button>

                    {status !== "revoked" && (
                      <button
                        type="button"
                        onClick={handleRevoke}
                        disabled={!canManage || revokeConnector.isPending}
                        className="px-3 py-1.5 bg-red-50 hover:bg-red-100 border border-red-200 text-red-700 rounded text-xs font-medium transition flex items-center gap-1.5 ml-auto disabled:opacity-50 disabled:cursor-not-allowed"
                        title="Disconnect and revoke connector credential"
                      >
                        <PowerOff className="h-3.5 w-3.5" />
                        Disconnect
                      </button>
                    )}
                  </div>

                  {!canManage && (
                    <p className="text-[11px] text-textMuted italic flex items-center gap-1">
                      <Lock className="h-3 w-3 text-textMuted" />
                      Read-only mode switch and connector operations require Owner or Manager role.
                    </p>
                  )}
                </>
              )}
            </CardContent>
          </Card>

          {/* Tenant Configuration & Security Boundary */}
          <Card className="border border-appBorder shadow-sm bg-white">
            <CardHeader>
              <div className="flex items-center justify-between mb-2">
                <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                  <ShieldCheck className="h-5 w-5 text-[#2A5BDB]" />
                </div>
                <Badge variant="primary">Postgres RLS (ADR 002)</Badge>
              </div>
              <CardTitle className="text-base text-[#102C54]">
                Tenant Context & Vault Security
              </CardTitle>
              <CardDescription className="text-xs text-textSecondary">
                Cryptographic envelope encryption and fail-closed database boundary.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-xs text-textSecondary space-y-3">
              <div className="border-b border-appBorder pb-2">
                <span className="text-textMuted block">Active Tenant Scope</span>
                <span className="font-semibold text-textPrimary font-mono">
                  {currentUser?.tenant_id || "Standby (connect FastAPI to resolve tenant UUID)"}
                </span>
              </div>
              <div className="border-b border-appBorder pb-2">
                <span className="text-textMuted block">OAuth Vault Cryptography</span>
                <span className="font-semibold text-textPrimary">
                  AES-256-GCM Envelope Encryption (per-record DEK, master KEK wrapping)
                </span>
              </div>
              <div className="border-b border-appBorder pb-2">
                <span className="text-textMuted block">Database Row-Level Security</span>
                <span className="font-semibold text-textPrimary font-mono text-[11px]">
                  FORCE ROW LEVEL SECURITY + security_invoker views (Migration 0013)
                </span>
              </div>
              <div>
                <span className="text-textMuted block">Rate Limiting & Idempotency</span>
                <span className="text-textPrimary">
                  Redis sliding-window + atomic PostgreSQL IdempotencyGate.
                </span>
              </div>
            </CardContent>
          </Card>

          {/* AI Model Routing Matrix (ADR 007 Four-Tier Governance) */}
          <Card className="md:col-span-2 border border-appBorder shadow-sm bg-white">
            <CardHeader>
              <div className="flex items-center justify-between mb-2">
                <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                  <Cpu className="h-5 w-5 text-[#2A5BDB]" />
                </div>
                <Badge variant="primary">ADR 007: 4-Tier Routing</Badge>
              </div>
              <CardTitle className="text-base text-[#102C54]">
                AI Model Routing Matrix (ADR 003 & ADR 007)
              </CardTitle>
              <CardDescription className="text-xs text-textSecondary">
                Environment-configured 4-tier gateway. Real (non-synthetic) PII is strictly
                restricted to the fail-closed Private route.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
                <div className="p-3 bg-[#F8F9FD] rounded-small border border-appBorder">
                  <span className="text-textMuted block font-mono uppercase text-[10px]">
                    cheap tier
                  </span>
                  <span className="font-bold text-[#102C54] text-sm block mt-0.5">
                    High-Throughput Extraction
                  </span>
                  <span className="text-[11px] text-textSecondary block mt-1">
                    DocuVerify OCR structuring & bulk candidate screening (synthetic or
                    zero-retention).
                  </span>
                </div>
                <div className="p-3 bg-[#F8F9FD] rounded-small border border-appBorder">
                  <span className="text-textMuted block font-mono uppercase text-[10px]">
                    strong tier
                  </span>
                  <span className="font-bold text-[#102C54] text-sm block mt-0.5">
                    Policy RAG & Synthesis
                  </span>
                  <span className="text-[11px] text-textSecondary block mt-1">
                    Bilingual English/Urdu Policy Brain & multi-constraint rubric analysis.
                  </span>
                </div>
                <div className="p-3 bg-[#F8F9FD] rounded-small border border-appBorder">
                  <span className="text-textMuted block font-mono uppercase text-[10px]">
                    reasoning tier
                  </span>
                  <span className="font-bold text-[#102C54] text-sm block mt-0.5">
                    Deep Compliance Critique
                  </span>
                  <span className="text-[11px] text-textSecondary block mt-1">
                    Complex statutory edge cases, contract audit, and multi-step rubric review.
                  </span>
                </div>
                <div className="p-3 bg-[#102C54]/5 rounded-small border border-[#2A5BDB]/30">
                  <span className="text-[#2A5BDB] block font-mono uppercase text-[10px] font-bold">
                    private tier (fail-closed)
                  </span>
                  <span className="font-bold text-[#102C54] text-sm block mt-0.5">
                    Real PII Only (VPC / Private)
                  </span>
                  <span className="text-[11px] text-textSecondary block mt-1">
                    Mandatory for real CNICs, IBANs, and salaries. Never falls back to public APIs.
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
