"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, CheckCircle2, RefreshCw, ShieldAlert } from "lucide-react";
import type { OnboardingStepSaveRequest } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { Step1ConnectTools } from "@/components/onboarding/step1-connect-tools";
import { Step2UploadPolicies } from "@/components/onboarding/step2-upload-policies";
import { Step3CompanyStructure } from "@/components/onboarding/step3-company-structure";
import { Step4EmployeeImport } from "@/components/onboarding/step4-employee-import";
import { Step5TestLaunch } from "@/components/onboarding/step5-test-launch";
import { WizardStepper } from "@/components/onboarding/wizard-stepper";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useHydrated } from "@/hooks/use-hydrated";
import { useOnboardingState, useSaveOnboardingStep } from "@/hooks/use-onboarding";
import { isOfflineError } from "@/lib/api-request";

export default function OnboardingPage() {
  const hydrated = useHydrated();
  const { data: currentUser } = useCurrentUser();
  const {
    isStandby,
    useFakeIntegrations,
    refetch: refetchHealth,
    isFetching: isHealthFetching,
  } = useBackendStatus();
  const {
    data: onboardingState,
    isLoading: isStateLoading,
    error: stateError,
    refetch: refetchState,
  } = useOnboardingState();
  const saveStepMutation = useSaveOnboardingStep();

  const [activeStep, setActiveStep] = useState<number>(1);
  const [stepSaveError, setStepSaveError] = useState<string | null>(null);
  const [stepSaveSuccess, setStepSaveSuccess] = useState<string | null>(null);

  // Hydrate activeStep from server state ONLY ONCE so window-focus refetches
  // never override a user who clicked back to an earlier step.
  const hasHydratedStepRef = useRef(false);

  useEffect(() => {
    if (onboardingState && !hasHydratedStepRef.current) {
      hasHydratedStepRef.current = true;
      const clamped = Math.min(5, Math.max(1, onboardingState.current_step || 1));
      setActiveStep(clamped);
    }
  }, [onboardingState]);

  const effectiveOnboardingState = hydrated ? onboardingState : undefined;
  const effectiveStateLoading = hydrated ? isStateLoading : false;
  const isOffline = hydrated && (isStandby || isOfflineError(stateError));
  const liveHttpError =
    stepSaveError ||
    (hydrated && stateError && !isOfflineError(stateError) ? stateError.message : null);

  const handleSaveStep = async (
    step: number,
    payload: OnboardingStepSaveRequest = { advance_step: true }
  ) => {
    setStepSaveError(null);
    setStepSaveSuccess(null);
    try {
      const updated = await saveStepMutation.mutateAsync({ step, payload });
      const nextStep = Math.min(5, Math.max(1, updated.current_step || step + 1));
      setActiveStep(nextStep);
      setStepSaveSuccess(`Saved Step ${step} progress.`);
    } catch (err: unknown) {
      setStepSaveError(err instanceof Error ? err.message : `Failed to save Step ${step}.`);
    }
  };

  const handleSelectStep = (step: number) => {
    setStepSaveError(null);
    setStepSaveSuccess(null);
    setActiveStep(Math.min(5, Math.max(1, step)));
  };

  const handleRetryBackend = () => {
    refetchHealth();
    refetchState();
  };

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      {/* Header & Breadcrumbs */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-appBorder pb-5">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              href="/"
              className="text-xs text-textSecondary hover:text-[#2A5BDB] flex items-center gap-1 font-medium transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              Dashboard
            </Link>
            <span className="text-textMuted text-xs">/</span>
            <span className="text-xs text-[#102C54] font-semibold">Onboarding Wizard</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[#102C54]">
            Workspace Onboarding Wizard
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Complete the 5-step guided setup to connect Google Workspace, upload policies, import
            employees, and launch your tenant workspace.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Badge variant="primary">{currentUser?.tenant_name || "Workspace"}</Badge>
          {effectiveOnboardingState?.is_live ? (
            <Badge variant="success">Live</Badge>
          ) : (
            <Badge variant={useFakeIntegrations ? "warning" : "success"}>
              {useFakeIntegrations ? "Fake Mode (ADR 006)" : "Live Mode (ADR 006)"}
            </Badge>
          )}
        </div>
      </div>

      {/* StandbyBanner ONLY when FastAPI is genuinely offline */}
      {isOffline && (
        <StandbyBanner
          title="Onboarding Wizard in Standby Mode"
          message="FastAPI backend is currently unreachable. Connect FastAPI to save onboarding progress, upload policies, and import employees to PostgreSQL."
          onRetry={handleRetryBackend}
          isRetrying={isHealthFetching}
        />
      )}

      {/* Explicit Live HTTP Error Alert (401/403/500 — never masked as Standby) */}
      {liveHttpError && !isOffline && (
        <div
          role="alert"
          className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900 shadow-sm"
        >
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{liveHttpError}</span>
          </div>
          <button
            type="button"
            onClick={() => {
              setStepSaveError(null);
              refetchState();
            }}
            className="inline-flex items-center gap-1 rounded border border-rose-300 bg-white px-2.5 py-1 text-xs font-semibold text-rose-800 hover:bg-rose-100"
          >
            <RefreshCw className="h-3 w-3" aria-hidden="true" />
            Retry
          </button>
        </div>
      )}

      {stepSaveSuccess && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center justify-between rounded-medium border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs font-medium text-emerald-800"
        >
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
            <span>{stepSaveSuccess}</span>
          </div>
          <button
            type="button"
            onClick={() => setStepSaveSuccess(null)}
            className="font-bold text-emerald-700 hover:text-emerald-900 px-1"
            aria-label="Dismiss message"
          >
            &times;
          </button>
        </div>
      )}

      {/* Horizontal Progress Bar & Stepper */}
      <WizardStepper
        currentStep={activeStep}
        completedSteps={effectiveOnboardingState?.completed_steps ?? []}
        isLiveWorkspace={effectiveOnboardingState?.is_live ?? false}
        onSelectStep={handleSelectStep}
      />

      {/* Active Step Panel */}
      {effectiveStateLoading && !effectiveOnboardingState ? (
        <div
          aria-busy="true"
          className="h-64 animate-pulse rounded-medium border border-appBorder bg-white p-6"
        />
      ) : (
        <>
          {activeStep === 1 && (
            <Step1ConnectTools
              isSavingStep={saveStepMutation.isPending}
              onSaveAndContinue={() => void handleSaveStep(1, { advance_step: true })}
            />
          )}
          {activeStep === 2 && (
            <Step2UploadPolicies
              isSavingStep={saveStepMutation.isPending}
              onBack={() => handleSelectStep(1)}
              onSaveAndContinue={() => void handleSaveStep(2, { advance_step: true })}
            />
          )}
          {activeStep === 3 && (
            <Step3CompanyStructure
              onboardingState={effectiveOnboardingState}
              isSavingStep={saveStepMutation.isPending}
              onBack={() => handleSelectStep(2)}
              onSaveAndContinue={(payload) => void handleSaveStep(3, payload)}
            />
          )}
          {activeStep === 4 && (
            <Step4EmployeeImport
              isSavingStep={saveStepMutation.isPending}
              onBack={() => handleSelectStep(3)}
              onSaveAndContinue={() => void handleSaveStep(4, { advance_step: true })}
            />
          )}
          {activeStep === 5 && (
            <Step5TestLaunch
              onboardingState={effectiveOnboardingState}
              onBack={() => handleSelectStep(4)}
              onNavigateToStep={handleSelectStep}
            />
          )}
        </>
      )}
    </div>
  );
}
