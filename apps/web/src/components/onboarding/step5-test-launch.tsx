"use client";

import React, { useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  RefreshCw,
  Rocket,
  Users2,
  XCircle,
} from "lucide-react";
import type { OnboardingLaunchResponse, OnboardingState } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useLaunchWorkspace, useOnboardingReadiness } from "@/hooks/use-onboarding";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";

interface Step5TestLaunchProps {
  onboardingState?: OnboardingState;
  onBack: () => void;
  onNavigateToStep: (step: number) => void;
}

export function Step5TestLaunch({
  onboardingState,
  onBack,
  onNavigateToStep,
}: Step5TestLaunchProps) {
  const { data: currentUser } = useCurrentUser();
  const {
    data: readiness,
    isLoading,
    error: readinessError,
    refetch,
    isFetching,
  } = useOnboardingReadiness();
  const launchWorkspace = useLaunchWorkspace();

  // Stable per-attempt idempotency key so retries safely replay the same launch intent
  const launchKeyRef = useRef<string | null>(null);

  const [launchResult, setLaunchResult] = useState<OnboardingLaunchResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const isAlreadyLive = Boolean(launchResult?.is_live || onboardingState?.is_live);
  const launchedAtDisplay = launchResult?.launched_at || onboardingState?.launched_at || null;
  const userRole = currentUser?.role?.toLowerCase() || "owner";
  const canLaunchRole = userRole === "owner" || currentUser?.is_hr_head === true;
  const canGoLive = Boolean(readiness?.can_go_live);

  const liveReadinessError =
    readinessError && !isOfflineError(readinessError) ? readinessError.message : null;

  const checklistItems = [
    {
      key: "connectors",
      label: "Google Workspace Connectors OK",
      ok: readiness?.connectors_ok ?? false,
      count: readiness?.counts.connectors ?? 0,
      targetStep: 1,
      detail: readiness?.connectors_ok
        ? "Connected and verified under ADR-006 scopes."
        : "Connect Google Workspace in Step 1 before launch.",
    },
    {
      key: "policies",
      label: "At least 1 Policy Uploaded",
      ok: readiness?.policies_ok ?? false,
      count: readiness?.counts.policies ?? 0,
      targetStep: 2,
      detail: readiness?.policies_ok
        ? `${readiness?.counts.policies ?? 0} policy document(s) staged.`
        : "Upload at least 1 HR policy document in Step 2.",
    },
    {
      key: "departments",
      label: "At least 1 Department Configured",
      ok: readiness?.departments_ok ?? false,
      count: readiness?.counts.departments ?? 0,
      targetStep: 3,
      detail: readiness?.departments_ok
        ? `${readiness?.counts.departments ?? 0} department(s) active.`
        : "Create at least 1 department in Step 3.",
    },
    {
      key: "employees",
      label: "At least 1 Employee Added",
      ok: readiness?.employees_ok ?? false,
      count: readiness?.counts.employees ?? 0,
      targetStep: 4,
      detail: readiness?.employees_ok
        ? `${readiness?.counts.employees ?? 0} employee record(s) committed.`
        : "Import or add at least 1 employee in Step 4.",
    },
  ];

  const handleGoLive = async () => {
    if (!canGoLive || !canLaunchRole || isAlreadyLive) return;
    if (!launchKeyRef.current) {
      const randomPart =
        typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      launchKeyRef.current = `onboarding-launch-${randomPart}`;
    }
    setErrorMessage(null);
    try {
      const res = await launchWorkspace.mutateAsync({
        idempotencyKey: launchKeyRef.current,
      });
      setLaunchResult(res);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Workspace launch failed.");
    }
  };

  return (
    <div className="space-y-6">
      {(errorMessage || liveReadinessError) && (
        <div
          role="alert"
          className="flex items-center justify-between rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{errorMessage || liveReadinessError}</span>
          </div>
          {errorMessage && (
            <button
              type="button"
              onClick={() => setErrorMessage(null)}
              className="font-bold text-rose-700 hover:text-rose-900 px-1"
              aria-label="Dismiss error"
            >
              &times;
            </button>
          )}
        </div>
      )}

      {/* Workspace Already Live or Just Launched Banner */}
      {isAlreadyLive && (
        <div
          role="status"
          aria-live="polite"
          className="rounded-medium border border-emerald-200 bg-emerald-50 p-5 shadow-sm"
        >
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-5 w-5 text-emerald-600 shrink-0" aria-hidden="true" />
                <h3 className="text-base font-bold text-[#102C54]">Workspace is Live</h3>
                <Badge variant="success">Launched</Badge>
              </div>
              <p className="text-xs text-emerald-900">
                Launched at <span className="font-semibold">{formatPKT(launchedAtDisplay)}</span>.
                {launchResult && (
                  <>
                    {" "}
                    Invitations dispatched: <strong>
                      {launchResult.invitations_sent}
                    </strong> sent, <strong>{launchResult.invitations_skipped}</strong> skipped •{" "}
                    <strong>{launchResult.sheets_queued}</strong> Sheets sync row(s) queued.
                  </>
                )}
              </p>
            </div>
            <Link
              href="/employees"
              className="inline-flex items-center gap-2 rounded-small bg-[#102C54] px-4 py-2 text-xs font-semibold text-white hover:bg-[#2A5BDB] transition-colors shrink-0"
            >
              <Users2 className="h-4 w-4" aria-hidden="true" />
              Open Employee Directory
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </Link>
          </div>
        </div>
      )}

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base text-[#102C54]">
                Step 5 — Readiness Checklist &amp; Workspace Launch
              </CardTitle>
              <CardDescription className="text-xs text-textSecondary">
                All four deterministic readiness gates must be green before launching your tenant
                workspace and sending onboarding invitations.
              </CardDescription>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              Refresh Checklist
            </Button>
          </div>
        </CardHeader>

        <CardContent className="space-y-6">
          {isLoading ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 animate-pulse" aria-busy="true">
              {Array.from({ length: 4 }).map((_, idx) => (
                <div key={idx} className="h-24 rounded-small bg-slate-100" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {checklistItems.map((item) => (
                <div
                  key={item.key}
                  className={`flex flex-col justify-between gap-3 rounded-small border p-4 ${
                    item.ok
                      ? "border-emerald-200 bg-emerald-50/70"
                      : "border-amber-200 bg-amber-50/80"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-start gap-2.5">
                      {item.ok ? (
                        <CheckCircle2
                          className="mt-0.5 h-5 w-5 text-emerald-600 shrink-0"
                          aria-hidden="true"
                        />
                      ) : (
                        <AlertTriangle
                          className="mt-0.5 h-5 w-5 text-amber-600 shrink-0"
                          aria-hidden="true"
                        />
                      )}
                      <div>
                        <p className="text-xs font-bold text-[#102C54]">{item.label}</p>
                        <p className="mt-0.5 text-[11px] text-textSecondary">{item.detail}</p>
                      </div>
                    </div>
                    <Badge variant={item.ok ? "success" : "warning"}>
                      {item.ok ? "Ready" : "Pending"}
                    </Badge>
                  </div>

                  {!item.ok && (
                    <div>
                      <button
                        type="button"
                        onClick={() => onNavigateToStep(item.targetStep)}
                        className="text-[11px] font-semibold text-[#2A5BDB] hover:underline"
                      >
                        Fix in Step {item.targetStep} &rarr;
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Go-Live CTA Bar */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-medium border border-appBorder bg-[#F8F9FD] p-4">
            <div className="text-xs text-textSecondary">
              {isAlreadyLive ? (
                <span className="font-semibold text-emerald-800">
                  Workspace launch has already been completed for this tenant.
                </span>
              ) : canGoLive ? (
                <span className="font-semibold text-emerald-800">
                  All readiness checks passed. Ready to launch workspace and invite employees.
                </span>
              ) : (
                <span className="font-medium text-amber-900">
                  Complete all 4 checklist items above to enable Go Live.
                </span>
              )}
              {!canLaunchRole && (
                <span className="mt-1 block text-[11px] text-rose-700">
                  Only Owner or HR Head roles can execute Go Live.
                </span>
              )}
            </div>

            <Button
              type="button"
              onClick={() => void handleGoLive()}
              disabled={!canGoLive || !canLaunchRole || isAlreadyLive || launchWorkspace.isPending}
              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] disabled:cursor-not-allowed disabled:opacity-50 inline-flex items-center gap-2 shrink-0"
            >
              <Rocket className="h-4 w-4" aria-hidden="true" />
              {launchWorkspace.isPending
                ? "Launching Workspace..."
                : isAlreadyLive
                  ? "Workspace Live"
                  : "Go Live & Send Invitations"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between pt-2">
        <Button type="button" variant="outline" onClick={onBack}>
          <ArrowLeft className="mr-1.5 h-4 w-4" aria-hidden="true" />
          Back
        </Button>
      </div>
    </div>
  );
}
