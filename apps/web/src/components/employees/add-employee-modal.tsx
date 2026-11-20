"use client";

import React, { useEffect, useId, useState } from "react";
import { UserPlus, X, XCircle } from "lucide-react";
import type { Department, EmployeeCreateRequest, EmployeeSummary } from "@agentic-hr/contracts";
import { Button } from "@/components/ui/button";
import { useCreateEmployee } from "@/hooks/use-employees";
import { getTodayPKTDateString } from "@/lib/format";

interface AddEmployeeModalProps {
  onClose: () => void;
  departments: Department[];
  managerOptions: EmployeeSummary[];
  canEditSensitive: boolean;
}

export function AddEmployeeModal({
  onClose,
  departments,
  managerOptions,
  canEditSensitive,
}: AddEmployeeModalProps) {
  const titleId = useId();
  const fullNameId = useId();
  const emailId = useId();
  const phoneId = useId();
  const deptId = useId();
  const mgrId = useId();
  const jobTitleId = useId();
  const empTypeId = useId();
  const joinDateId = useId();
  const cnicId = useId();
  const bankId = useId();

  const createEmployee = useCreateEmployee();

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [managerId, setManagerId] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [employmentType, setEmploymentType] = useState<
    "full_time" | "part_time" | "contract" | "intern"
  >("full_time");
  const [joinDate, setJoinDate] = useState(() => getTodayPKTDateString());
  const [cnic, setCnic] = useState("");
  const [bankAccount, setBankAccount] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    if (!fullName.trim() || !email.trim() || !jobTitle.trim() || !joinDate) {
      setErrorMessage("Full name, work email, job title, and join date are required.");
      return;
    }

    const payload: EmployeeCreateRequest = {
      full_name: fullName.trim(),
      email: email.trim().toLowerCase(),
      phone: phone.trim() ? phone.trim() : null,
      department_id: departmentId || null,
      manager_id: managerId || null,
      job_title: jobTitle.trim(),
      employment_type: employmentType,
      join_date: joinDate,
      status: "active",
    };

    if (canEditSensitive) {
      if (cnic.trim()) payload.cnic = cnic.trim();
      if (bankAccount.trim()) payload.bank_account = bankAccount.trim();
    }

    try {
      await createEmployee.mutateAsync(payload);
      onClose();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to create employee record.");
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#102C54]/50 p-4 backdrop-blur-[1px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-2xl max-h-[90vh] overflow-y-auto overscroll-contain rounded-medium border border-appBorder bg-white shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-appBorder px-6 py-4 bg-[#F8F9FD]">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-small bg-[#2A5BDB]/10 text-[#2A5BDB]">
              <UserPlus className="h-4 w-4" aria-hidden="true" />
            </div>
            <div>
              <h2 id={titleId} className="text-base font-bold text-[#102C54]">
                Add New Employee
              </h2>
              <p className="text-[11px] text-textSecondary">
                Persists to PostgreSQL with automatic Google Sheets sync queueing.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close add employee modal"
            className="rounded-small p-1.5 text-textSecondary hover:bg-slate-200/70 hover:text-[#102C54]"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4 p-6 text-xs">
          {errorMessage && (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-small border border-rose-200 bg-rose-50 px-3 py-2.5 text-rose-900"
            >
              <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
              <span className="font-medium">{errorMessage}</span>
            </div>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1">
              <label htmlFor={fullNameId} className="block font-semibold text-textPrimary">
                Full Name <span aria-hidden="true">*</span>
              </label>
              <input
                id={fullNameId}
                name="full_name"
                type="text"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="e.g. Ayesha Siddiqa"
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor={emailId} className="block font-semibold text-textPrimary">
                Work Email <span aria-hidden="true">*</span>
              </label>
              <input
                id={emailId}
                name="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="ayesha@company.com"
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor={phoneId} className="block font-semibold text-textPrimary">
                Phone (Optional)
              </label>
              <input
                id={phoneId}
                name="phone"
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+92-300-1234567"
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor={jobTitleId} className="block font-semibold text-textPrimary">
                Job Title <span aria-hidden="true">*</span>
              </label>
              <input
                id={jobTitleId}
                name="job_title"
                type="text"
                required
                value={jobTitle}
                onChange={(e) => setJobTitle(e.target.value)}
                placeholder="e.g. Senior Backend Engineer"
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor={deptId} className="block font-semibold text-textPrimary">
                Department
              </label>
              <select
                id={deptId}
                name="department_id"
                value={departmentId}
                onChange={(e) => setDepartmentId(e.target.value)}
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              >
                <option value="">Unassigned</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <label htmlFor={mgrId} className="block font-semibold text-textPrimary">
                Reporting Manager
              </label>
              <select
                id={mgrId}
                name="manager_id"
                value={managerId}
                onChange={(e) => setManagerId(e.target.value)}
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              >
                <option value="">None (Top-Level Root)</option>
                {managerOptions.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.full_name} ({m.job_title})
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <label htmlFor={empTypeId} className="block font-semibold text-textPrimary">
                Employment Type
              </label>
              <select
                id={empTypeId}
                name="employment_type"
                value={employmentType}
                onChange={(e) =>
                  setEmploymentType(
                    e.target.value as "full_time" | "part_time" | "contract" | "intern"
                  )
                }
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              >
                <option value="full_time">Full Time</option>
                <option value="part_time">Part Time</option>
                <option value="contract">Contract</option>
                <option value="intern">Intern</option>
              </select>
            </div>

            <div className="space-y-1">
              <label htmlFor={joinDateId} className="block font-semibold text-textPrimary">
                Join Date <span aria-hidden="true">*</span>
              </label>
              <input
                id={joinDateId}
                name="join_date"
                type="date"
                required
                value={joinDate}
                onChange={(e) => setJoinDate(e.target.value)}
                className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
              />
            </div>
          </div>

          {canEditSensitive && (
            <div className="rounded-small border border-appBorder bg-[#F8F9FD] p-3.5 space-y-3">
              <p className="text-[11px] font-bold uppercase tracking-wider text-[#102C54]">
                Encrypted Sensitive Identifiers (AES-256-GCM)
              </p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <label htmlFor={cnicId} className="block font-semibold text-textPrimary">
                    CNIC (Optional)
                  </label>
                  <input
                    id={cnicId}
                    name="cnic"
                    type="text"
                    value={cnic}
                    onChange={(e) => setCnic(e.target.value)}
                    placeholder="42101-1234567-1"
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 font-mono text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor={bankId} className="block font-semibold text-textPrimary">
                    Bank Account / IBAN (Optional)
                  </label>
                  <input
                    id={bankId}
                    name="bank_account"
                    type="text"
                    value={bankAccount}
                    onChange={(e) => setBankAccount(e.target.value)}
                    placeholder="PK36ACME000100001234"
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 font-mono text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                  />
                </div>
              </div>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 border-t border-appBorder pt-4">
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={createEmployee.isPending}
              className="bg-[#2A5BDB] text-white hover:bg-[#102C54]"
            >
              {createEmployee.isPending ? "Saving Employee..." : "Create Employee"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
