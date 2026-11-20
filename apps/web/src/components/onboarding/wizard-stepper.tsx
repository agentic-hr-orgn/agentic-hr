"use client";

import React from "react";
import { Check } from "lucide-react";

export interface WizardStepMeta {
  step: number;
  title: string;
  shortTitle: string;
  description: string;
}

export const ONBOARDING_STEPS: WizardStepMeta[] = [
  {
    step: 1,
    title: "Connect Tools",
    shortTitle: "Tools",
    description: "Google Workspace & ADR 006 scopes",
  },
  {
    step: 2,
    title: "Upload Policies",
    shortTitle: "Policies",
    description: "HR handbooks (PDF, DOCX, TXT)",
  },
  {
    step: 3,
    title: "Company Structure",
    shortTitle: "Structure",
    description: "Departments, roles & Stage 6 defaults",
  },
  {
    step: 4,
    title: "Add Employees",
    shortTitle: "Employees",
    description: "Excel bulk import & PII validation",
  },
  {
    step: 5,
    title: "Test & Launch",
    shortTitle: "Launch",
    description: "Readiness checks & workspace go-live",
  },
];

interface WizardStepperProps {
  currentStep: number;
  completedSteps: number[];
  isLiveWorkspace?: boolean;
  onSelectStep: (step: number) => void;
}

export function WizardStepper({
  currentStep,
  completedSteps,
  isLiveWorkspace = false,
  onSelectStep,
}: WizardStepperProps) {
  const completedSet = new Set<number>(completedSteps);
  if (isLiveWorkspace) {
    for (const s of ONBOARDING_STEPS) {
      completedSet.add(s.step);
    }
  }
  const completedCount = ONBOARDING_STEPS.filter((s) => completedSet.has(s.step)).length;
  const progressPercent = Math.round((completedCount / ONBOARDING_STEPS.length) * 100);

  return (
    <div className="rounded-medium border border-appBorder bg-white p-4 md:p-5 shadow-sm space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-[#102C54]">
            Setup Progress
          </p>
          <p className="text-xs text-textSecondary">
            Step {currentStep} of {ONBOARDING_STEPS.length} • {completedCount} of{" "}
            {ONBOARDING_STEPS.length} completed
          </p>
        </div>
        <span className="inline-flex items-center rounded-full border border-[#2A5BDB]/20 bg-[#2A5BDB]/10 px-2.5 py-0.5 text-xs font-bold text-[#2A5BDB]">
          {progressPercent}% Complete
        </span>
      </div>

      {/* Horizontal Progress Bar */}
      <div
        role="progressbar"
        aria-label="Onboarding setup progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progressPercent}
        className="h-2 w-full overflow-hidden rounded-full bg-slate-100"
      >
        <div
          className="h-full rounded-full bg-[#2A5BDB] transition-all duration-300"
          style={{ width: `${progressPercent}%` }}
        />
      </div>

      {/* Horizontal 5-Step Stepper */}
      <nav aria-label="Onboarding steps">
        <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {ONBOARDING_STEPS.map((item) => {
            const isActive = currentStep === item.step;
            const isCompleted = completedSet.has(item.step);

            return (
              <li key={item.step}>
                <button
                  type="button"
                  onClick={() => onSelectStep(item.step)}
                  aria-current={isActive ? "step" : undefined}
                  className={`flex w-full items-start gap-3 rounded-small border p-3 text-left transition-all ${
                    isActive
                      ? "border-[#2A5BDB] bg-[#F8F9FD] ring-1 ring-[#2A5BDB]/30 shadow-sm"
                      : isCompleted
                        ? "border-emerald-200 bg-emerald-50/40 hover:bg-emerald-50/80"
                        : "border-appBorder bg-white hover:bg-[#F8F9FD]"
                  }`}
                >
                  <span
                    className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                      isActive
                        ? "bg-[#2A5BDB] text-white"
                        : isCompleted
                          ? "bg-emerald-600 text-white"
                          : "bg-slate-100 text-[#102C54]"
                    }`}
                  >
                    {isCompleted && !isActive ? (
                      <Check className="h-3.5 w-3.5" aria-hidden="true" />
                    ) : (
                      item.step
                    )}
                  </span>
                  <div className="min-w-0">
                    <span
                      className={`block text-xs font-bold leading-snug ${
                        isActive ? "text-[#102C54]" : "text-textPrimary"
                      }`}
                    >
                      {item.title}
                    </span>
                    <span className="block text-[11px] text-textSecondary truncate">
                      {item.description}
                    </span>
                  </div>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
    </div>
  );
}
