"use client";

import React, { useCallback, useEffect, useId, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  CheckCircle2,
  Plus,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";
import type { OnboardingState, OnboardingStepSaveRequest } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  LeaveAttendanceConfigPanel,
  type LeaveConfigWizardSnapshot,
} from "@/components/leave/leave-attendance-config-panel";
import { useCreateDepartment, useDeleteDepartment, useDepartments } from "@/hooks/use-departments";
import { useCurrentUser } from "@/hooks/use-current-user";
import { isOfflineError } from "@/lib/api-request";

interface Step3CompanyStructureProps {
  onboardingState?: OnboardingState;
  isSavingStep: boolean;
  onBack: () => void;
  onSaveAndContinue: (payload: OnboardingStepSaveRequest) => void;
}

const BUSINESS_TYPES = [
  "Software / IT Services",
  "E-Commerce",
  "Manufacturing",
  "Consulting",
  "Healthcare",
  "Financial Services",
  "Other",
];

const ROLES_MATRIX: Array<{
  role: string;
  directory: string;
  edits: string;
  pii: string;
  onboarding: string;
  approvals: string;
}> = [
  {
    role: "Owner",
    directory: "Full tenant",
    edits: "All records",
    pii: "Decrypt & edit CNIC / IBAN",
    onboarding: "Full + Go Live",
    approvals: "Approve / Override",
  },
  {
    role: "HR Head",
    directory: "Full tenant",
    edits: "All records",
    pii: "Decrypt & edit CNIC / IBAN",
    onboarding: "Full + Go Live",
    approvals: "HR & Policy decisions",
  },
  {
    role: "Payroll Admin",
    directory: "Full tenant",
    edits: "Payroll / PII",
    pii: "Decrypt & edit CNIC / IBAN",
    onboarding: "Read-only",
    approvals: "Payroll overrides",
  },
  {
    role: "Manager",
    directory: "Full tenant",
    edits: "Non-PII fields",
    pii: "Masked / Hidden (null)",
    onboarding: "Steps 1–4 setup",
    approvals: "Team leave approvals",
  },
  {
    role: "Employee",
    directory: "Non-PII view",
    edits: "None (Read-only)",
    pii: "Self-view only",
    onboarding: "Read-only",
    approvals: "Submit & resubmit",
  },
];

export function Step3CompanyStructure({
  onboardingState,
  isSavingStep,
  onBack,
  onSaveAndContinue,
}: Step3CompanyStructureProps) {
  const businessSelectId = useId();
  const deptNameInputId = useId();
  const deptCodeInputId = useId();
  const matrixCaptionId = useId();

  const { data: currentUser } = useCurrentUser();
  const { data: departments = [], isLoading, error: deptError } = useDepartments();
  const createDepartment = useCreateDepartment();
  const deleteDepartment = useDeleteDepartment();

  const [businessType, setBusinessType] = useState<string>(
    onboardingState?.business_type || "Software / IT Services"
  );
  const [deptName, setDeptName] = useState("");
  const [deptCode, setDeptCode] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [leaveSnapshot, setLeaveSnapshot] = useState<LeaveConfigWizardSnapshot | null>(null);

  useEffect(() => {
    if (onboardingState?.business_type) {
      setBusinessType(onboardingState.business_type);
    }
  }, [onboardingState?.business_type]);

  const userRole = currentUser?.role?.toLowerCase() || "owner";
  const canManage =
    userRole === "owner" || userRole === "manager" || currentUser?.is_hr_head === true;
  const canManageLeaveConfig = userRole === "owner" || currentUser?.is_hr_head === true;

  const liveDeptError = deptError && !isOfflineError(deptError) ? deptError.message : null;

  const handleSnapshotChange = useCallback((snapshot: LeaveConfigWizardSnapshot) => {
    setLeaveSnapshot(snapshot);
  }, []);

  const handleAddDepartment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canManage) return;
    const cleanName = deptName.trim();
    if (!cleanName) {
      setErrorMessage("Department name is required.");
      return;
    }
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      const created = await createDepartment.mutateAsync({
        name: cleanName,
        code: deptCode.trim() ? deptCode.trim().toUpperCase() : null,
      });
      setDeptName("");
      setDeptCode("");
      setSuccessMessage(`Added department '${created.name}'.`);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to create department.");
    }
  };

  const handleDeleteDepartment = async (id: string, name: string) => {
    if (!canManage) return;
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      await deleteDepartment.mutateAsync(id);
      setSuccessMessage(`Removed department '${name}'.`);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to remove department.");
    }
  };

  const handleContinue = () => {
    const existingConfig =
      onboardingState?.structure_config && typeof onboardingState.structure_config === "object"
        ? onboardingState.structure_config
        : {};

    onSaveAndContinue({
      business_type: businessType,
      advance_step: true,
      structure_config: {
        ...existingConfig,
        weekends: leaveSnapshot?.weekends ?? ["Saturday", "Sunday"],
        probation_months: leaveSnapshot?.probation_months ?? 3,
        check_in_enabled: leaveSnapshot?.check_in_enabled ?? true,
        leave_rules: leaveSnapshot?.leave_rules ?? {
          annual_days: 20,
          sick_days: 10,
          casual_days: 10,
          carry_forward_max: 5,
        },
        holidays: leaveSnapshot?.holidays ?? [],
      },
    });
  };

  return (
    <div className="space-y-6">
      {(errorMessage || liveDeptError) && (
        <div
          role="alert"
          className="flex items-center justify-between rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{errorMessage || liveDeptError}</span>
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

      {successMessage && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 rounded-medium border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-medium text-emerald-800"
        >
          <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
          <span>{successMessage}</span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Business Type & Departments Manager */}
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardHeader>
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-medium bg-[#2A5BDB]/10">
                <Building2 className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              </div>
              <div>
                <CardTitle className="text-base text-[#102C54]">
                  Step 3 — Company Structure &amp; Departments
                </CardTitle>
                <CardDescription className="text-xs text-textSecondary">
                  Configure your business profile and department taxonomy before bulk import.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-1.5">
              <label
                htmlFor={businessSelectId}
                className="block text-xs font-semibold text-textPrimary"
              >
                Business Type
              </label>
              <select
                id={businessSelectId}
                name="business_type"
                value={businessType}
                onChange={(e) => setBusinessType(e.target.value)}
                disabled={!canManage}
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              >
                {BUSINESS_TYPES.map((bt) => (
                  <option key={bt} value={bt}>
                    {bt}
                  </option>
                ))}
              </select>
            </div>

            <form
              onSubmit={handleAddDepartment}
              className="space-y-3 border-t border-appBorder pt-4"
            >
              <p className="text-xs font-bold uppercase tracking-wider text-[#102C54]">
                Add Department
              </p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <div className="sm:col-span-2 space-y-1">
                  <label
                    htmlFor={deptNameInputId}
                    className="block text-[11px] font-semibold text-textSecondary"
                  >
                    Department Name
                  </label>
                  <input
                    id={deptNameInputId}
                    name="department_name"
                    type="text"
                    value={deptName}
                    onChange={(e) => setDeptName(e.target.value)}
                    placeholder="e.g. Engineering"
                    disabled={!canManage || createDepartment.isPending}
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-1.5 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                  />
                </div>
                <div className="space-y-1">
                  <label
                    htmlFor={deptCodeInputId}
                    className="block text-[11px] font-semibold text-textSecondary"
                  >
                    Code (Optional)
                  </label>
                  <input
                    id={deptCodeInputId}
                    name="department_code"
                    type="text"
                    maxLength={12}
                    value={deptCode}
                    onChange={(e) => setDeptCode(e.target.value)}
                    placeholder="ENG"
                    disabled={!canManage || createDepartment.isPending}
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-1.5 text-xs uppercase text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                  />
                </div>
              </div>
              <Button
                type="submit"
                size="sm"
                disabled={!canManage || createDepartment.isPending}
                className="bg-[#102C54] text-white hover:bg-[#2A5BDB] inline-flex items-center gap-1.5"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                {createDepartment.isPending ? "Adding..." : "Add Department"}
              </Button>
            </form>

            <div className="space-y-2 border-t border-appBorder pt-4">
              <p className="text-xs font-bold uppercase tracking-wider text-[#102C54]">
                Configured Departments ({departments.length})
              </p>
              {isLoading ? (
                <div className="h-16 animate-pulse rounded-small bg-slate-100" aria-busy="true" />
              ) : departments.length === 0 ? (
                <p className="rounded-small border border-dashed border-appBorder bg-[#F8F9FD] p-3 text-xs text-textSecondary">
                  No departments configured yet. Add at least 1 department before launching.
                </p>
              ) : (
                <ul className="space-y-2">
                  {departments.map((dept) => (
                    <li
                      key={dept.id}
                      className="flex items-center justify-between gap-2 rounded-small border border-appBorder bg-[#F8F9FD] px-3 py-2 text-xs"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-semibold text-[#102C54] truncate">{dept.name}</span>
                        {dept.code && (
                          <Badge variant="outline" className="font-mono text-[10px]">
                            {dept.code}
                          </Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[11px] text-textSecondary">
                          {dept.employee_count} employees
                        </span>
                        {canManage && (
                          <button
                            type="button"
                            onClick={() => void handleDeleteDepartment(dept.id, dept.name)}
                            disabled={deleteDepartment.isPending}
                            aria-label={`Remove department ${dept.name}`}
                            className="rounded p-1 text-rose-600 hover:bg-rose-100 disabled:opacity-50"
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Roles & Permissions Matrix */}
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardHeader>
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              <CardTitle className="text-base text-[#102C54]">
                Roles &amp; Permissions Matrix (ADR 002)
              </CardTitle>
            </div>
            <CardDescription className="text-xs text-textSecondary">
              Encrypted PII (CNIC &amp; bank IBAN) is strictly restricted to Owner, HR Head, Payroll
              Admin, and employee self-view.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div
              tabIndex={0}
              role="region"
              aria-labelledby={matrixCaptionId}
              className="overflow-x-auto rounded-small border border-appBorder"
            >
              <table className="w-full border-collapse text-left text-[11px]">
                <caption id={matrixCaptionId} className="sr-only">
                  Agentic HR role-based access and PII boundaries matrix
                </caption>
                <thead className="bg-[#102C54] text-white">
                  <tr>
                    <th scope="col" className="px-2.5 py-2 font-semibold">
                      Role
                    </th>
                    <th scope="col" className="px-2.5 py-2 font-semibold">
                      Directory
                    </th>
                    <th scope="col" className="px-2.5 py-2 font-semibold">
                      Encrypted PII
                    </th>
                    <th scope="col" className="px-2.5 py-2 font-semibold">
                      Approvals
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-appBorder bg-white">
                  {ROLES_MATRIX.map((row) => (
                    <tr key={row.role} className="hover:bg-[#F8F9FD]">
                      <th scope="row" className="px-2.5 py-2 font-bold text-[#102C54]">
                        {row.role}
                      </th>
                      <td className="px-2.5 py-2 text-textSecondary">{row.directory}</td>
                      <td className="px-2.5 py-2 font-medium text-[#2A5BDB]">{row.pii}</td>
                      <td className="px-2.5 py-2 text-textSecondary">{row.approvals}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Live Stage 6 Leave, Work-Week & Holiday Rules */}
      <LeaveAttendanceConfigPanel
        mode="wizard"
        canManage={canManageLeaveConfig}
        isOffline={isOfflineError(deptError)}
        onConfigSnapshotChange={handleSnapshotChange}
      />

      <div className="flex items-center justify-between pt-2">
        <Button type="button" variant="outline" onClick={onBack}>
          <ArrowLeft className="mr-1.5 h-4 w-4" aria-hidden="true" />
          Back
        </Button>
        <Button
          type="button"
          onClick={handleContinue}
          disabled={isSavingStep || !canManage}
          className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-2"
        >
          {isSavingStep ? "Saving Step 3..." : "Save & Continue to Employee Import"}
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
