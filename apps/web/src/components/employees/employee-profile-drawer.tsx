"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import {
  CheckCircle2,
  Download,
  Edit3,
  Eye,
  EyeOff,
  FileText,
  Lock,
  ShieldCheck,
  Trash2,
  UploadCloud,
  X,
  XCircle,
} from "lucide-react";
import type { Department, EmployeeSummary, EmployeeUpdateRequest } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/hooks/use-current-user";
import {
  useDeleteEmployeeDocument,
  useDownloadEmployeeDocument,
  useEmployeeDocuments,
  useEmployeeProfile,
  useUpdateEmployee,
  useUploadEmployeeDocument,
} from "@/hooks/use-employees";
import { isOfflineError } from "@/lib/api-request";
import { formatPKT, getPKTDaysUntil } from "@/lib/format";
import { useToastStore } from "@/stores/toast-store";

interface EmployeeProfileDrawerProps {
  employeeId: string | null;
  onClose: () => void;
  departments: Department[];
  managerOptions: EmployeeSummary[];
}

interface DrawerInnerProps {
  employeeId: string;
  onClose: () => void;
  departments: Department[];
  managerOptions: EmployeeSummary[];
}

function getExpiryBadgeProps(expiryDateIso: string | null | undefined): {
  label: string;
  className: string;
} | null {
  if (!expiryDateIso) return null;
  const days = getPKTDaysUntil(expiryDateIso);
  if (days === null) {
    return {
      label: `Expires ${expiryDateIso}`,
      className: "border-slate-200 bg-slate-50 text-slate-700",
    };
  }
  if (days < 0) {
    return {
      label: `Expired (${Math.abs(days)}d ago)`,
      className: "border-rose-300 bg-rose-50 text-rose-800 font-semibold",
    };
  }
  if (days <= 7) {
    return {
      label: days === 0 ? "Expires today" : `Expires in ${days}d`,
      className: "border-rose-300 bg-rose-50 text-rose-800 font-semibold",
    };
  }
  if (days <= 30) {
    return {
      label: `Expires in ${days}d`,
      className: "border-amber-300 bg-amber-50 text-amber-800 font-semibold",
    };
  }
  return {
    label: `Expires ${expiryDateIso}`,
    className: "border-slate-200 bg-slate-50 text-slate-700",
  };
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function EmployeeProfileDrawerContent({
  employeeId,
  onClose,
  departments,
  managerOptions,
}: DrawerInnerProps) {
  const titleId = useId();
  const fullNameId = useId();
  const phoneId = useId();
  const jobTitleId = useId();
  const deptSelectId = useId();
  const mgrSelectId = useId();
  const empTypeSelectId = useId();
  const statusSelectId = useId();
  const cnicInputId = useId();
  const bankInputId = useId();

  const docTypeId = useId();
  const docTitleId = useId();
  const docExpiryId = useId();
  const docFileId = useId();
  const docFileInputRef = useRef<HTMLInputElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const pushToast = useToastStore((s) => s.pushToast);

  const { data: currentUser } = useCurrentUser();
  const {
    data: profile,
    isLoading: isProfileLoading,
    error: profileError,
  } = useEmployeeProfile(employeeId);
  const updateEmployee = useUpdateEmployee();

  const {
    data: documents = [],
    isLoading: isDocsLoading,
    error: docsError,
  } = useEmployeeDocuments(employeeId);
  const uploadDoc = useUploadEmployeeDocument(employeeId);
  const downloadDoc = useDownloadEmployeeDocument();
  const deleteDoc = useDeleteEmployeeDocument(employeeId);

  const [activeTab, setActiveTab] = useState<"profile" | "documents">("profile");
  const [isRevealed, setIsRevealed] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [bannerError, setBannerError] = useState<string | null>(null);
  const [bannerSuccess, setBannerSuccess] = useState<string | null>(null);

  // Edit form draft states (populated explicitly on Edit button click)
  const [editFullName, setEditFullName] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editJobTitle, setEditJobTitle] = useState("");
  const [editDepartmentId, setEditDepartmentId] = useState("");
  const [editManagerId, setEditManagerId] = useState("");
  const [editEmploymentType, setEditEmploymentType] = useState<
    "full_time" | "part_time" | "contract" | "intern"
  >("full_time");
  const [editStatus, setEditStatus] = useState<
    "active" | "invited" | "on_leave" | "terminated" | "resigned"
  >("active");
  const [editCnic, setEditCnic] = useState("");
  const [editBankAccount, setEditBankAccount] = useState("");
  const [cnicDirty, setCnicDirty] = useState(false);
  const [bankDirty, setBankDirty] = useState(false);

  // Document Vault upload states
  const [docType, setDocType] = useState<"cnic" | "contract" | "certificate" | "other">("cnic");
  const [docTitle, setDocTitle] = useState("");
  const [docExpiry, setDocExpiry] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  useEffect(() => {
    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();

    const getFocusableElements = (): HTMLElement[] => {
      if (!dialogRef.current) return [];
      const selector =
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
      return Array.from(dialogRef.current.querySelectorAll<HTMLElement>(selector));
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "Tab") {
        const focusable = getFocusableElements();
        if (focusable.length === 0) {
          e.preventDefault();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey) {
          if (!active || active === first || !dialogRef.current?.contains(active)) {
            e.preventDefault();
            last.focus();
          }
        } else if (!active || active === last || !dialogRef.current?.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      if (previouslyFocusedRef.current && document.contains(previouslyFocusedRef.current)) {
        previouslyFocusedRef.current.focus();
      }
    };
  }, [onClose]);

  const userRole = currentUser?.role?.toLowerCase() || "employee";
  const canManageProfile =
    userRole === "owner" || userRole === "manager" || currentUser?.is_hr_head === true;
  const canDeleteDocs = userRole === "owner" || currentUser?.is_hr_head === true;

  const liveProfileError =
    profileError && !isOfflineError(profileError) ? profileError.message : null;
  const liveDocsError = docsError && !isOfflineError(docsError) ? docsError.message : null;
  const displayedError = bannerError || liveProfileError || liveDocsError;

  const handleStartEdit = () => {
    if (!profile) return;
    setBannerError(null);
    setBannerSuccess(null);
    setEditFullName(profile.full_name);
    setEditPhone(profile.phone ?? "");
    setEditJobTitle(profile.job_title);
    setEditDepartmentId(profile.department_id ?? "");
    setEditManagerId(profile.manager_id ?? "");
    setEditEmploymentType(
      (profile.employment_type as "full_time" | "part_time" | "contract" | "intern") || "full_time"
    );
    setEditStatus(
      (profile.status as "active" | "invited" | "on_leave" | "terminated" | "resigned") || "active"
    );
    setEditCnic(profile.cnic ?? "");
    setEditBankAccount(profile.bank_account ?? "");
    setCnicDirty(false);
    setBankDirty(false);
    setIsEditing(true);
  };

  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profile) return;
    setBannerError(null);
    setBannerSuccess(null);

    const payload: EmployeeUpdateRequest = {
      full_name: editFullName.trim(),
      phone: editPhone.trim() ? editPhone.trim() : null,
      job_title: editJobTitle.trim(),
      department_id: editDepartmentId || null,
      manager_id: editManagerId || null,
      employment_type: editEmploymentType,
      status: editStatus,
    };

    // Only send PII keys when caller has edit rights AND explicitly edited non-empty values
    if (profile.can_edit_sensitive) {
      if (cnicDirty && editCnic.trim()) {
        payload.cnic = editCnic.trim();
      }
      if (bankDirty && editBankAccount.trim()) {
        payload.bank_account = editBankAccount.trim();
      }
    }

    try {
      await updateEmployee.mutateAsync({ employeeId: profile.id, payload });
      setIsEditing(false);
      setBannerSuccess("Employee profile updated in PostgreSQL & queued for Sheets sync.");
      pushToast({
        variant: "success",
        title: "Employee profile updated",
        description: `${profile.full_name} saved in PostgreSQL & queued for Sheets sync.`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update employee profile.";
      setBannerError(msg);
      pushToast({ variant: "error", title: "Profile update failed", description: msg });
    }
  };

  const handleUploadDocument = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) {
      setBannerError("Please choose a document file (PDF, PNG, JPEG, or DOCX).");
      return;
    }
    setBannerError(null);
    setBannerSuccess(null);
    try {
      const uploaded = await uploadDoc.mutateAsync({
        file: selectedFile,
        documentType: docType,
        title: docTitle.trim() || undefined,
        expiryDate: docExpiry.trim() || undefined,
      });
      setDocTitle("");
      setDocExpiry("");
      setSelectedFile(null);
      if (docFileInputRef.current) {
        docFileInputRef.current.value = "";
      }
      setBannerSuccess(`Uploaded '${uploaded.title}' to ${uploaded.storage_provider} vault.`);
      pushToast({
        variant: "success",
        title: "Document uploaded",
        description: `'${uploaded.title}' stored in ${uploaded.storage_provider} vault.`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to upload document.";
      setBannerError(msg);
      pushToast({ variant: "error", title: "Document upload failed", description: msg });
    }
  };

  const handleDownloadDocument = async (documentId: string, fileName: string) => {
    setBannerError(null);
    try {
      await downloadDoc.mutateAsync({ employeeId, documentId, fileName });
    } catch (err: unknown) {
      setBannerError(err instanceof Error ? err.message : "Failed to download document.");
    }
  };

  const handleDeleteDocument = async (documentId: string, title: string) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Are you sure you want to delete '${title}'?`)
    ) {
      return;
    }
    setBannerError(null);
    setBannerSuccess(null);
    try {
      await deleteDoc.mutateAsync(documentId);
      setBannerSuccess(`Deleted document '${title}'.`);
      pushToast({
        variant: "success",
        title: "Document deleted",
        description: `Removed '${title}'.`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to delete document.";
      setBannerError(msg);
      pushToast({ variant: "error", title: "Delete failed", description: msg });
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-[#102C54]/50 backdrop-blur-[1px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex h-full w-full max-w-2xl flex-col border-l border-appBorder bg-white shadow-2xl focus:outline-none"
      >
        {/* Drawer Header */}
        <div className="flex items-start justify-between border-b border-appBorder bg-[#F8F9FD] px-6 py-4">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id={titleId} className="text-lg font-bold text-[#102C54] truncate">
                {profile?.full_name || "Employee Profile"}
              </h2>
              {profile?.employee_code && (
                <Badge variant="outline" className="font-mono text-[10px]">
                  {profile.employee_code}
                </Badge>
              )}
              {profile?.status && (
                <Badge variant={profile.status === "active" ? "success" : "warning"}>
                  {profile.status.replace("_", " ")}
                </Badge>
              )}
            </div>
            <p className="text-xs text-textSecondary truncate">
              {profile
                ? `${profile.job_title} • ${profile.department_name || "Unassigned"}`
                : "Loading employee profile..."}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close employee profile drawer"
            className="rounded-small p-1.5 text-textSecondary hover:bg-slate-200/70 hover:text-[#102C54]"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Drawer Tabs */}
        <div
          role="tablist"
          aria-label="Employee drawer sections"
          className="flex gap-2 border-b border-appBorder bg-white px-6 pt-3"
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "profile"}
            onClick={() => setActiveTab("profile")}
            className={`border-b-2 px-3 py-2 text-xs font-bold transition-colors ${
              activeTab === "profile"
                ? "border-[#2A5BDB] text-[#2A5BDB]"
                : "border-transparent text-textSecondary hover:text-[#102C54]"
            }`}
          >
            Profile &amp; PII
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "documents"}
            onClick={() => setActiveTab("documents")}
            className={`border-b-2 px-3 py-2 text-xs font-bold transition-colors ${
              activeTab === "documents"
                ? "border-[#2A5BDB] text-[#2A5BDB]"
                : "border-transparent text-textSecondary hover:text-[#102C54]"
            }`}
          >
            Document Vault ({documents.length})
          </button>
        </div>

        {/* Scrollable Body */}
        <div className="flex-1 overflow-y-auto overscroll-contain p-6 space-y-5 text-xs">
          {displayedError && (
            <div
              role="alert"
              className="flex items-center justify-between gap-2 rounded-small border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-rose-900"
            >
              <div className="flex items-center gap-2">
                <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
                <span className="font-medium">{displayedError}</span>
              </div>
              {bannerError && (
                <button
                  type="button"
                  onClick={() => setBannerError(null)}
                  className="font-bold text-rose-700 hover:text-rose-900"
                  aria-label="Dismiss error"
                >
                  &times;
                </button>
              )}
            </div>
          )}

          {bannerSuccess && (
            <div
              role="status"
              aria-live="polite"
              className="flex items-center justify-between gap-2 rounded-small border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-emerald-900"
            >
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
                <span className="font-medium">{bannerSuccess}</span>
              </div>
              <button
                type="button"
                onClick={() => setBannerSuccess(null)}
                className="font-bold text-emerald-700"
                aria-label="Dismiss status"
              >
                &times;
              </button>
            </div>
          )}

          {/* PROFILE & PII TAB */}
          {activeTab === "profile" && (
            <>
              {isProfileLoading ? (
                <div className="space-y-3 animate-pulse" aria-busy="true">
                  <div className="h-24 rounded-small bg-slate-100" />
                  <div className="h-32 rounded-small bg-slate-100" />
                </div>
              ) : profile ? (
                <div className="space-y-6">
                  {/* Edit Toggle Bar */}
                  {canManageProfile && !isEditing && (
                    <div className="flex justify-end">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={handleStartEdit}
                        className="inline-flex items-center gap-1.5 text-xs"
                      >
                        <Edit3 className="h-3.5 w-3.5" aria-hidden="true" />
                        Edit Profile
                      </Button>
                    </div>
                  )}

                  {isEditing ? (
                    <form
                      onSubmit={(e) => void handleSaveEdit(e)}
                      className="space-y-4 rounded-small border border-appBorder bg-[#F8F9FD] p-4"
                    >
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <div className="space-y-1">
                          <label
                            htmlFor={fullNameId}
                            className="block font-semibold text-textPrimary"
                          >
                            Full Name
                          </label>
                          <input
                            id={fullNameId}
                            type="text"
                            required
                            value={editFullName}
                            onChange={(e) => setEditFullName(e.target.value)}
                            className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                          />
                        </div>
                        <div className="space-y-1">
                          <label htmlFor={phoneId} className="block font-semibold text-textPrimary">
                            Phone
                          </label>
                          <input
                            id={phoneId}
                            type="tel"
                            value={editPhone}
                            onChange={(e) => setEditPhone(e.target.value)}
                            className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                          />
                        </div>
                        <div className="space-y-1">
                          <label
                            htmlFor={jobTitleId}
                            className="block font-semibold text-textPrimary"
                          >
                            Job Title
                          </label>
                          <input
                            id={jobTitleId}
                            type="text"
                            required
                            value={editJobTitle}
                            onChange={(e) => setEditJobTitle(e.target.value)}
                            className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                          />
                        </div>
                        <div className="space-y-1">
                          <label
                            htmlFor={deptSelectId}
                            className="block font-semibold text-textPrimary"
                          >
                            Department
                          </label>
                          <select
                            id={deptSelectId}
                            value={editDepartmentId}
                            onChange={(e) => setEditDepartmentId(e.target.value)}
                            className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
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
                          <label
                            htmlFor={mgrSelectId}
                            className="block font-semibold text-textPrimary"
                          >
                            Manager
                          </label>
                          <select
                            id={mgrSelectId}
                            value={editManagerId}
                            onChange={(e) => setEditManagerId(e.target.value)}
                            className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                          >
                            <option value="">None (Root)</option>
                            {managerOptions
                              .filter((m) => m.id !== profile.id)
                              .map((m) => (
                                <option key={m.id} value={m.id}>
                                  {m.full_name}
                                </option>
                              ))}
                          </select>
                        </div>
                        <div className="space-y-1">
                          <label
                            htmlFor={empTypeSelectId}
                            className="block font-semibold text-textPrimary"
                          >
                            Employment Type
                          </label>
                          <select
                            id={empTypeSelectId}
                            value={editEmploymentType}
                            onChange={(e) =>
                              setEditEmploymentType(
                                e.target.value as "full_time" | "part_time" | "contract" | "intern"
                              )
                            }
                            className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                          >
                            <option value="full_time">Full Time</option>
                            <option value="part_time">Part Time</option>
                            <option value="contract">Contract</option>
                            <option value="intern">Intern</option>
                          </select>
                        </div>
                        <div className="space-y-1">
                          <label
                            htmlFor={statusSelectId}
                            className="block font-semibold text-textPrimary"
                          >
                            Status
                          </label>
                          <select
                            id={statusSelectId}
                            value={editStatus}
                            onChange={(e) =>
                              setEditStatus(
                                e.target.value as
                                  "active" | "invited" | "on_leave" | "terminated" | "resigned"
                              )
                            }
                            className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                          >
                            <option value="active">Active</option>
                            <option value="invited">Invited</option>
                            <option value="on_leave">On Leave</option>
                            <option value="terminated">Terminated</option>
                            <option value="resigned">Resigned</option>
                          </select>
                        </div>
                      </div>

                      {profile.can_edit_sensitive && (
                        <div className="grid grid-cols-1 gap-3 border-t border-appBorder pt-3 sm:grid-cols-2">
                          <div className="space-y-1">
                            <label
                              htmlFor={cnicInputId}
                              className="block font-semibold text-[#102C54]"
                            >
                              Update CNIC
                            </label>
                            <input
                              id={cnicInputId}
                              type="text"
                              value={editCnic}
                              onChange={(e) => {
                                setEditCnic(e.target.value);
                                setCnicDirty(true);
                              }}
                              placeholder="42101-1234567-1"
                              className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-xs"
                            />
                          </div>
                          <div className="space-y-1">
                            <label
                              htmlFor={bankInputId}
                              className="block font-semibold text-[#102C54]"
                            >
                              Update Bank Account / IBAN
                            </label>
                            <input
                              id={bankInputId}
                              type="text"
                              value={editBankAccount}
                              onChange={(e) => {
                                setEditBankAccount(e.target.value);
                                setBankDirty(true);
                              }}
                              placeholder="PK36ACME000100001234"
                              className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 font-mono text-xs"
                            />
                          </div>
                        </div>
                      )}

                      <div className="flex justify-end gap-2 pt-2">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => setIsEditing(false)}
                        >
                          Cancel
                        </Button>
                        <Button
                          type="submit"
                          size="sm"
                          disabled={updateEmployee.isPending}
                          className="bg-[#2A5BDB] text-white hover:bg-[#102C54]"
                        >
                          {updateEmployee.isPending ? "Saving..." : "Save Changes"}
                        </Button>
                      </div>
                    </form>
                  ) : (
                    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 rounded-small border border-appBorder bg-[#F8F9FD] p-4">
                      <div>
                        <dt className="text-[11px] text-textMuted">Work Email</dt>
                        <dd className="font-mono font-semibold text-textPrimary">
                          {profile.email}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[11px] text-textMuted">Phone</dt>
                        <dd className="font-medium text-textPrimary">{profile.phone || "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-[11px] text-textMuted">Department</dt>
                        <dd className="font-medium text-textPrimary">
                          {profile.department_name || "Unassigned"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[11px] text-textMuted">Manager</dt>
                        <dd className="font-medium text-textPrimary">
                          {profile.manager_name || "Top-level root"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[11px] text-textMuted">Employment Type</dt>
                        <dd className="capitalize font-medium text-textPrimary">
                          {profile.employment_type.replace("_", " ")}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[11px] text-textMuted">Join Date</dt>
                        <dd className="font-mono font-medium text-textPrimary">
                          {profile.join_date}
                        </dd>
                      </div>
                      <div className="sm:col-span-2">
                        <dt className="text-[11px] text-textMuted">Last Updated (PKT)</dt>
                        <dd className="text-[11px] text-textSecondary">
                          {formatPKT(profile.updated_at)}
                        </dd>
                      </div>
                    </dl>
                  )}

                  {/* ENCRYPTED PII VAULT SECTION */}
                  <div className="rounded-medium border border-appBorder bg-white p-4 shadow-sm space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <ShieldCheck className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                        <h3 className="text-sm font-bold text-[#102C54]">Encrypted PII Vault</h3>
                      </div>
                      {profile.can_view_sensitive && (
                        <div className="flex items-center gap-2">
                          <Badge variant="primary" className="text-[10px]">
                            AES-256-GCM • Audit-Logged
                          </Badge>
                          <button
                            type="button"
                            aria-pressed={isRevealed}
                            onClick={() => setIsRevealed((prev) => !prev)}
                            className="inline-flex items-center gap-1.5 rounded-small border border-[#2A5BDB]/30 bg-[#F8F9FD] px-2.5 py-1 text-[11px] font-semibold text-[#2A5BDB] hover:bg-[#2A5BDB]/10"
                          >
                            {isRevealed ? (
                              <>
                                <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                                Hide
                              </>
                            ) : (
                              <>
                                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                                Reveal
                              </>
                            )}
                          </button>
                        </div>
                      )}
                    </div>

                    {profile.can_view_sensitive ? (
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <div className="rounded-small border border-appBorder bg-[#F8F9FD] p-3">
                          <span className="block text-[11px] text-textMuted">CNIC</span>
                          <span
                            data-testid="pii-cnic-value"
                            className="mt-0.5 block font-mono text-xs font-bold text-[#102C54]"
                          >
                            {isRevealed
                              ? profile.cnic || "Not set"
                              : profile.cnic_masked || "Not set"}
                          </span>
                        </div>
                        <div className="rounded-small border border-appBorder bg-[#F8F9FD] p-3">
                          <span className="block text-[11px] text-textMuted">
                            Bank Account / IBAN
                          </span>
                          <span
                            data-testid="pii-bank-value"
                            className="mt-0.5 block font-mono text-xs font-bold text-[#102C54]"
                          >
                            {isRevealed
                              ? profile.bank_account || "Not set"
                              : profile.bank_account_masked || "Not set"}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-start gap-2.5 rounded-small border border-amber-200 bg-amber-50 p-3 text-amber-900">
                        <Lock
                          className="h-4 w-4 text-amber-600 shrink-0 mt-0.5"
                          aria-hidden="true"
                        />
                        <div>
                          <p className="font-semibold">
                            Restricted — Owner, HR Head, Payroll Admin, or Self Only
                          </p>
                          <p className="mt-0.5 text-[11px]">
                            Sensitive CNIC and bank account fields are never decrypted for
                            unauthorized roles.
                          </p>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ) : null}
            </>
          )}

          {/* DOCUMENT VAULT TAB */}
          {activeTab === "documents" && (
            <div className="space-y-5">
              {canManageProfile && (
                <form
                  onSubmit={(e) => void handleUploadDocument(e)}
                  className="space-y-3 rounded-medium border border-appBorder bg-[#F8F9FD] p-4"
                >
                  <p className="font-bold text-[#102C54]">Upload Employee Document</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <div className="space-y-1">
                      <label htmlFor={docTypeId} className="block font-semibold text-textPrimary">
                        Document Type
                      </label>
                      <select
                        id={docTypeId}
                        value={docType}
                        onChange={(e) =>
                          setDocType(
                            e.target.value as "cnic" | "contract" | "certificate" | "other"
                          )
                        }
                        className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                      >
                        <option value="cnic">CNIC</option>
                        <option value="contract">Contract</option>
                        <option value="certificate">Certificate</option>
                        <option value="other">Other</option>
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label htmlFor={docTitleId} className="block font-semibold text-textPrimary">
                        Title (Optional)
                      </label>
                      <input
                        id={docTitleId}
                        type="text"
                        value={docTitle}
                        onChange={(e) => setDocTitle(e.target.value)}
                        placeholder="e.g. Signed Contract"
                        className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                      />
                    </div>
                    <div className="space-y-1">
                      <label htmlFor={docExpiryId} className="block font-semibold text-textPrimary">
                        Expiry Date (Optional)
                      </label>
                      <input
                        id={docExpiryId}
                        type="date"
                        value={docExpiry}
                        onChange={(e) => setDocExpiry(e.target.value)}
                        className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                      />
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
                    <div className="space-y-1">
                      <label htmlFor={docFileId} className="block font-semibold text-textPrimary">
                        File (PDF, PNG, JPEG, DOCX — max 10MB)
                      </label>
                      <input
                        ref={docFileInputRef}
                        id={docFileId}
                        type="file"
                        accept=".pdf,.png,.jpg,.jpeg,.docx"
                        onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
                        className="text-xs text-textSecondary"
                      />
                    </div>
                    <Button
                      type="submit"
                      size="sm"
                      disabled={uploadDoc.isPending || !selectedFile}
                      className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1.5"
                    >
                      <UploadCloud className="h-3.5 w-3.5" aria-hidden="true" />
                      {uploadDoc.isPending ? "Uploading..." : "Upload Document"}
                    </Button>
                  </div>
                </form>
              )}

              {isDocsLoading ? (
                <div className="space-y-2 animate-pulse" aria-busy="true">
                  <div className="h-14 rounded-small bg-slate-100" />
                  <div className="h-14 rounded-small bg-slate-100" />
                </div>
              ) : documents.length === 0 ? (
                <div className="rounded-small border border-dashed border-appBorder bg-[#F8F9FD] p-6 text-center text-textSecondary">
                  No documents uploaded for this employee yet.
                </div>
              ) : (
                <ul className="space-y-2.5">
                  {documents.map((doc) => {
                    const expiryBadge = getExpiryBadgeProps(doc.expiry_date);
                    return (
                      <li
                        key={doc.id}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-small border border-appBorder bg-white p-3.5"
                      >
                        <div className="flex items-start gap-3 min-w-0">
                          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-small bg-[#102C54]/5 text-[#102C54]">
                            <FileText className="h-4 w-4" aria-hidden="true" />
                          </div>
                          <div className="min-w-0 space-y-1">
                            <p className="font-bold text-[#102C54] truncate">{doc.title}</p>
                            <div className="flex flex-wrap items-center gap-1.5">
                              <Badge variant="outline" className="uppercase text-[10px]">
                                {doc.document_type}
                              </Badge>
                              <Badge variant="secondary" className="text-[10px]">
                                {doc.storage_provider === "google_drive"
                                  ? "Google Drive"
                                  : "Local Vault"}
                              </Badge>
                              {expiryBadge && (
                                <Badge
                                  variant="outline"
                                  className={`text-[10px] ${expiryBadge.className}`}
                                >
                                  {expiryBadge.label}
                                </Badge>
                              )}
                              <span className="text-[11px] text-textMuted">
                                {formatBytes(doc.size_bytes)}
                              </span>
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center gap-2 shrink-0">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => void handleDownloadDocument(doc.id, doc.file_name)}
                            disabled={downloadDoc.isPending}
                            className="text-xs h-7 px-2.5 inline-flex items-center gap-1"
                          >
                            <Download className="h-3 w-3" aria-hidden="true" />
                            Download
                          </Button>
                          {canDeleteDocs && (
                            <button
                              type="button"
                              onClick={() => void handleDeleteDocument(doc.id, doc.title)}
                              disabled={deleteDoc.isPending}
                              aria-label={`Delete document ${doc.title}`}
                              className="inline-flex items-center gap-1 rounded-small border border-rose-200 bg-rose-50 px-2.5 py-1 text-[11px] font-semibold text-rose-700 hover:bg-rose-100 disabled:opacity-50"
                            >
                              <Trash2 className="h-3 w-3" aria-hidden="true" />
                              Delete
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function EmployeeProfileDrawer({
  employeeId,
  onClose,
  departments,
  managerOptions,
}: EmployeeProfileDrawerProps) {
  if (!employeeId) return null;
  return (
    <EmployeeProfileDrawerContent
      key={employeeId}
      employeeId={employeeId}
      onClose={onClose}
      departments={departments}
      managerOptions={managerOptions}
    />
  );
}
