"use client";

import React, { useId, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Database,
  FileSpreadsheet,
  Network,
  RefreshCw,
  Search,
  ShieldAlert,
  UserPlus,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { AddEmployeeModal } from "@/components/employees/add-employee-modal";
import { EmployeeProfileDrawer } from "@/components/employees/employee-profile-drawer";
import { OrgChartView } from "@/components/employees/org-chart-view";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useDepartments } from "@/hooks/use-departments";
import { useEmployees } from "@/hooks/use-employees";
import { useHydrated } from "@/hooks/use-hydrated";
import { isOfflineError } from "@/lib/api-request";
import { formatPKTTime } from "@/lib/format";

const PAGE_SIZE = 15;

export default function EmployeesPage() {
  const searchInputId = useId();
  const deptSelectId = useId();
  const statusSelectId = useId();
  const typeSelectId = useId();
  const tableCaptionId = useId();

  const hydrated = useHydrated();
  const { data: currentUser } = useCurrentUser();
  const {
    isStandby: isBackendStandby,
    useFakeIntegrations,
    refetch: refetchHealth,
  } = useBackendStatus();
  const { data: departments = [] } = useDepartments();

  const [viewMode, setViewMode] = useState<"directory" | "org-chart">("directory");
  const [search, setSearch] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [employmentType, setEmploymentType] = useState("");
  const [page, setPage] = useState(1);

  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string | null>(null);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [manualReloadedAt, setManualReloadedAt] = useState<string | null>(null);

  const { data, isLoading, isFetching, error, refetch, dataUpdatedAt } = useEmployees({
    search,
    departmentId,
    status: statusFilter,
    employmentType,
    page,
    pageSize: PAGE_SIZE,
  });

  const effectiveDepartments = hydrated ? departments : [];
  const effectiveIsLoading = hydrated ? isLoading : false;
  const effectiveIsFetching = hydrated ? isFetching : false;

  const lastReloadedAt =
    manualReloadedAt || (hydrated && dataUpdatedAt ? formatPKTTime(new Date(dataUpdatedAt)) : null);

  const employees = hydrated ? (data?.items ?? []) : [];
  const total = hydrated ? (data?.total ?? 0) : 0;
  const totalPages = Math.max(1, hydrated ? (data?.total_pages ?? 1) : 1);

  const isOffline = hydrated && (isBackendStandby || isOfflineError(error));
  const liveHttpError = hydrated && error && !isOfflineError(error) ? error.message : null;

  const userRole = hydrated ? currentUser?.role?.toLowerCase() || "employee" : "employee";
  const canManage =
    hydrated &&
    (userRole === "owner" || userRole === "manager" || currentUser?.is_hr_head === true);
  const canEditSensitive =
    hydrated &&
    (userRole === "owner" ||
      currentUser?.is_hr_head === true ||
      currentUser?.is_payroll_admin === true);

  const handleReload = async () => {
    refetchHealth();
    await refetch();
    setManualReloadedAt(formatPKTTime(new Date()));
  };

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      {/* Top Header */}
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
            <span className="text-xs text-[#102C54] font-semibold">Directory</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[#102C54]">
            Employee Directory &amp; Sheets Synchronization
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            PostgreSQL 16 is authoritative (<code className="font-mono">pub_employees</code>);
            Google Sheets serves as a synced view (ADR 002 &amp; ADR 005).
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="primary">Postgres Authoritative</Badge>
          <Badge variant={isOffline ? "warning" : "success"}>
            {isOffline ? "Standby Sample" : "Live Feed"}
          </Badge>
        </div>
      </div>

      {/* StandbyBanner ONLY when genuinely offline */}
      {isOffline && (
        <StandbyBanner
          title="Directory in Standby Mode"
          message="FastAPI backend is currently unreachable. Live PostgreSQL directory records, encrypted profiles, and document vaults will load when connected."
          onRetry={() => void handleReload()}
          isRetrying={effectiveIsFetching}
        />
      )}

      {/* Explicit Live HTTP Error Alert (never masked as Standby) */}
      {liveHttpError && !isOffline && (
        <div
          role="alert"
          className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900 shadow-sm"
        >
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{liveHttpError}</span>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleReload()}
            disabled={effectiveIsFetching}
            className="border-rose-300 bg-white text-rose-800 hover:bg-rose-100 h-7 px-3 text-xs"
          >
            Retry
          </Button>
        </div>
      )}

      {/* View Switcher & Global Actions Bar */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        <div
          role="tablist"
          aria-label="Employee view mode"
          className="inline-flex rounded-small border border-appBorder bg-white p-1 shadow-sm"
        >
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === "directory"}
            onClick={() => setViewMode("directory")}
            className={`inline-flex items-center gap-1.5 rounded-small px-3.5 py-1.5 text-xs font-bold transition-colors ${
              viewMode === "directory"
                ? "bg-[#2A5BDB] text-white"
                : "text-textSecondary hover:text-[#102C54]"
            }`}
          >
            <Users className="h-3.5 w-3.5" aria-hidden="true" />
            Directory Table
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === "org-chart"}
            onClick={() => setViewMode("org-chart")}
            className={`inline-flex items-center gap-1.5 rounded-small px-3.5 py-1.5 text-xs font-bold transition-colors ${
              viewMode === "org-chart"
                ? "bg-[#2A5BDB] text-white"
                : "text-textSecondary hover:text-[#102C54]"
            }`}
          >
            <Network className="h-3.5 w-3.5" aria-hidden="true" />
            Org Chart
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {lastReloadedAt && (
            <p
              data-testid="last-reloaded-pkt"
              className="text-[11px] font-mono text-textSecondary mr-1"
            >
              Last reloaded: {lastReloadedAt}
            </p>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleReload()}
            disabled={effectiveIsFetching}
            className="bg-white text-xs inline-flex items-center gap-1.5"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${effectiveIsFetching ? "animate-spin" : ""}`}
              aria-hidden="true"
            />
            {effectiveIsFetching ? "Reloading..." : "Reload Postgres Data"}
          </Button>

          <Link
            href="/onboarding"
            className="inline-flex items-center gap-1.5 rounded-small border border-appBorder bg-white px-3 py-1.5 text-xs font-semibold text-[#102C54] hover:bg-slate-50"
          >
            <FileSpreadsheet className="h-3.5 w-3.5 text-[#2A5BDB]" aria-hidden="true" />
            Bulk Import (.xlsx)
          </Link>

          {canManage && (
            <Button
              type="button"
              size="sm"
              onClick={() => setIsAddModalOpen(true)}
              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1.5 text-xs"
            >
              <UserPlus className="h-3.5 w-3.5" aria-hidden="true" />
              Add Employee
            </Button>
          )}
        </div>
      </div>

      {viewMode === "directory" ? (
        <Card className="border border-appBorder bg-white shadow-sm">
          <CardHeader className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Database className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                <CardTitle className="text-base text-[#102C54]">
                  Employees ({effectiveIsLoading ? "..." : total})
                </CardTitle>
              </div>
              <span className="text-[11px] text-textSecondary">
                {useFakeIntegrations ? "Fake Integration Mode" : "Live Workspace Mode"} • Click any
                row to view profile &amp; document vault
              </span>
            </div>
            <CardDescription className="sr-only">
              Searchable and paginated employee directory queried from public.pub_employees
            </CardDescription>

            {/* Filters Bar (resets page to 1 on any filter update) */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1">
                <label
                  htmlFor={searchInputId}
                  className="block text-[11px] font-semibold text-textSecondary"
                >
                  Search Directory
                </label>
                <div className="relative">
                  <Search
                    className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400"
                    aria-hidden="true"
                  />
                  <input
                    id={searchInputId}
                    type="search"
                    value={search}
                    onChange={(e) => {
                      setSearch(e.target.value);
                      setPage(1);
                    }}
                    placeholder="Name, email, title, code..."
                    className="w-full rounded-small border border-slate-300 bg-white py-1.5 pl-8 pr-3 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label
                  htmlFor={deptSelectId}
                  className="block text-[11px] font-semibold text-textSecondary"
                >
                  Department
                </label>
                <select
                  id={deptSelectId}
                  value={departmentId}
                  onChange={(e) => {
                    setDepartmentId(e.target.value);
                    setPage(1);
                  }}
                  className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                >
                  <option value="">All Departments</option>
                  {effectiveDepartments.map((dept) => (
                    <option key={dept.id} value={dept.id}>
                      {dept.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1">
                <label
                  htmlFor={statusSelectId}
                  className="block text-[11px] font-semibold text-textSecondary"
                >
                  Status
                </label>
                <select
                  id={statusSelectId}
                  value={statusFilter}
                  onChange={(e) => {
                    setStatusFilter(e.target.value);
                    setPage(1);
                  }}
                  className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                >
                  <option value="">All Statuses</option>
                  <option value="active">Active</option>
                  <option value="invited">Invited</option>
                  <option value="on_leave">On Leave</option>
                  <option value="terminated">Terminated</option>
                  <option value="resigned">Resigned</option>
                </select>
              </div>

              <div className="space-y-1">
                <label
                  htmlFor={typeSelectId}
                  className="block text-[11px] font-semibold text-textSecondary"
                >
                  Employment Type
                </label>
                <select
                  id={typeSelectId}
                  value={employmentType}
                  onChange={(e) => {
                    setEmploymentType(e.target.value);
                    setPage(1);
                  }}
                  className="w-full rounded-small border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-textPrimary focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                >
                  <option value="">All Types</option>
                  <option value="full_time">Full Time</option>
                  <option value="part_time">Part Time</option>
                  <option value="contract">Contract</option>
                  <option value="intern">Intern</option>
                </select>
              </div>
            </div>
          </CardHeader>

          <CardContent className="space-y-4">
            {effectiveIsLoading ? (
              <div className="space-y-2.5 animate-pulse" aria-busy="true">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="h-10 rounded-small bg-slate-100" />
                ))}
              </div>
            ) : employees.length > 0 ? (
              <>
                <div
                  tabIndex={0}
                  role="region"
                  aria-labelledby={tableCaptionId}
                  className="overflow-x-auto rounded-small border border-appBorder"
                >
                  <table className="w-full border-collapse text-left text-xs">
                    <caption id={tableCaptionId} className="sr-only">
                      Non-sensitive employee directory table sourced from public.pub_employees
                    </caption>
                    <thead className="bg-[#102C54] text-white">
                      <tr>
                        <th scope="col" className="px-3 py-2.5 font-semibold">
                          Name
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-semibold">
                          Email
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-semibold">
                          Department
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-semibold">
                          Job Title
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-semibold">
                          Type
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-semibold">
                          Status
                        </th>
                        <th scope="col" className="px-3 py-2.5 font-semibold">
                          Join Date
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-appBorder bg-white">
                      {employees.map((emp) => (
                        <tr
                          key={emp.id}
                          onClick={() => setSelectedEmployeeId(emp.id)}
                          className="cursor-pointer transition-colors hover:bg-[#F8F9FD]"
                        >
                          <th scope="row" className="px-3 py-2.5 font-bold text-[#102C54]">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedEmployeeId(emp.id);
                              }}
                              className="text-left hover:text-[#2A5BDB] hover:underline"
                            >
                              {emp.full_name}
                            </button>
                          </th>
                          <td className="px-3 py-2.5 font-mono text-[11px] text-textSecondary">
                            {emp.email}
                          </td>
                          <td className="px-3 py-2.5 text-textSecondary">
                            {emp.department_name || "Unassigned"}
                          </td>
                          <td className="px-3 py-2.5 text-textPrimary">{emp.job_title}</td>
                          <td className="px-3 py-2.5 capitalize text-textSecondary">
                            {emp.employment_type.replace("_", " ")}
                          </td>
                          <td className="px-3 py-2.5">
                            <Badge
                              variant={emp.status === "active" ? "success" : "warning"}
                              className="text-[10px]"
                            >
                              {emp.status.replace("_", " ")}
                            </Badge>
                          </td>
                          <td className="px-3 py-2.5 font-mono text-[11px] text-textSecondary">
                            {emp.join_date}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Server-Side Pagination Controls */}
                <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs text-textSecondary">
                  <span>
                    Page <strong>{page}</strong> of <strong>{totalPages}</strong> •{" "}
                    <strong>{total}</strong> total employees
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={page <= 1}
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      className="h-7 px-2.5 text-xs inline-flex items-center gap-1"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                      Previous
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={page >= totalPages}
                      onClick={() => setPage((p) => p + 1)}
                      className="h-7 px-2.5 text-xs inline-flex items-center gap-1"
                    >
                      Next
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-small border border-dashed border-appBorder bg-[#F8F9FD] p-8 text-center text-xs text-textSecondary">
                <p className="font-semibold text-[#102C54]">
                  No employees found matching your filters.
                </p>
                <p className="mt-1">
                  Try clearing filters, adding an employee manually, or running Excel Bulk Import.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      ) : (
        <OrgChartView onSelectEmployee={(id) => setSelectedEmployeeId(id)} />
      )}

      {/* Add Employee Modal */}
      {isAddModalOpen && (
        <AddEmployeeModal
          onClose={() => setIsAddModalOpen(false)}
          departments={effectiveDepartments}
          managerOptions={employees}
          canEditSensitive={canEditSensitive}
        />
      )}

      {/* Employee Profile & Encrypted PII / Document Vault Drawer */}
      <EmployeeProfileDrawer
        employeeId={selectedEmployeeId}
        onClose={() => setSelectedEmployeeId(null)}
        departments={effectiveDepartments}
        managerOptions={employees}
      />
    </div>
  );
}
