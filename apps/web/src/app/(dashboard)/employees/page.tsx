"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Users, ArrowLeft, RefreshCw, Database } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useEmployees } from "@/hooks/use-employees";
import { formatPKTTime } from "@/lib/format";

export default function EmployeesPage() {
  const { data: currentUser } = useCurrentUser();
  const { useFakeIntegrations } = useBackendStatus();
  const tenantSlug = currentUser?.clerk_org_id || "acme-tech-01";
  const { data, isLoading, isFetching, error, refetch, dataUpdatedAt } = useEmployees(tenantSlug);
  const [showAll, setShowAll] = useState(false);
  const [lastReloadedAt, setLastReloadedAt] = useState<string | null>(null);

  useEffect(() => {
    if (dataUpdatedAt) {
      setLastReloadedAt(formatPKTTime(new Date(dataUpdatedAt)));
    }
  }, [dataUpdatedAt]);

  const handleReload = async () => {
    await refetch();
    setLastReloadedAt(formatPKTTime(new Date()));
  };

  const employees = data?.employees ?? [];
  const isStandby = data?.source === "standby" || !!error;
  const visibleEmployees = showAll ? employees : employees.slice(0, 10);

  return (
    <div className="space-y-6">
      {/* Top Banner */}
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
            <span className="text-xs text-textPrimary font-semibold">Directory</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[#102C54]">
            Employee Directory & Sheets Synchronization
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            PostgreSQL 16 is authoritative; Google Sheets serves as a designated synced view (ADR
            002 & ADR 005).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="primary">Postgres Authoritative</Badge>
          <Badge variant={isStandby ? "warning" : "success"}>
            {isStandby ? "Standby Sample" : "Live Feed"}
          </Badge>
        </div>
      </div>

      {/* Standby Notice */}
      {isStandby && (
        <StandbyBanner
          message={
            error
              ? `Directory API error: ${error.message}`
              : "Showing labelled sample directory records while FastAPI is offline. Live PostgreSQL records will load automatically when reconnected."
          }
          onRetry={() => refetch()}
          isRetrying={isFetching}
        />
      )}

      {/* Architecture Notice */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card className="md:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                <Database className="h-5 w-5" />
              </div>
              <Badge variant="primary">ADR 005: Sheets View</Badge>
            </div>
            <CardTitle className="text-base text-[#102C54]">
              One-Way Authoritative Sync & Drift Reconciliation
            </CardTitle>
            <CardDescription className="text-xs">
              PostgreSQL owns employee records; Google Sheets drift is audited and overwritten from
              Postgres truth.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <p>
              To ensure reliability without data corruption, PostgreSQL maintains authoritative
              ownership of employee, leave balance, and payroll state. Sync jobs drain via{" "}
              <code className="font-mono text-[#102C54]">SELECT ... FOR UPDATE SKIP LOCKED</code>{" "}
              and reconcile any unauthorized edits in Sheets automatically.
            </p>
            <div className="p-3 bg-[#F8F9FD] rounded-small border border-appBorder">
              <span className="font-semibold text-[#102C54] block mb-1">
                Active Tenant Dataset ({currentUser?.tenant_name || "Acme Tech Solutions"}):
              </span>
              <span>
                40 synthetic employees across HR (5), Engineering (15), Sales (10), and Operations
                (10) — preparing for Stage 4 Master Employee Records &amp; Encrypted CNIC/IBAN
                Vault.
              </span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-bold text-[#102C54]">Sync Controls</CardTitle>
            <CardDescription className="text-xs">
              {useFakeIntegrations ? "Fake Integration Mode (ADR 006)" : "Live Google Workspace"}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button
              variant="outline"
              size="sm"
              onClick={handleReload}
              disabled={isFetching}
              className="w-full text-xs whitespace-nowrap"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 mr-2 shrink-0 ${isFetching ? "animate-spin" : ""}`}
              />
              {isFetching ? "Reloading..." : "Reload Postgres Data"}
            </Button>
            {lastReloadedAt && (
              <p
                data-testid="last-reloaded-pkt"
                className="text-[11px] text-textMuted font-mono text-center"
              >
                Last reloaded: {lastReloadedAt}
              </p>
            )}
            <div className="text-[11px] text-textSecondary space-y-1">
              <p>• Tenant: {tenantSlug}</p>
              <p>• Mode: USE_FAKE_INTEGRATIONS={String(useFakeIntegrations)}</p>
              <p>• Source: {isStandby ? "Standby Sample" : "PostgreSQL / FastAPI"}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Employee List / Table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2 text-[#102C54]">
              <Users className="h-4 w-4 text-[#2A5BDB]" />
              <span>
                Employees ({isLoading ? "..." : employees.length}
                {isStandby ? " Sample" : ""})
              </span>
            </CardTitle>
            <Badge variant={isStandby ? "warning" : "outline"}>
              {isStandby ? "Standby Sample Dataset" : "Authoritative Directory Feed"}
            </Badge>
          </div>
          <CardDescription className="text-xs">
            {isStandby
              ? "Sample preview displayed because FastAPI is in standby mode."
              : "Live directory loaded from FastAPI backend."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2.5 animate-pulse" aria-busy="true">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="h-10 rounded-small bg-slate-100" />
              ))}
            </div>
          ) : visibleEmployees.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-appBorder bg-[#F8F9FD] text-textSecondary font-semibold">
                    <th className="p-2.5">Name</th>
                    <th className="p-2.5">Email</th>
                    <th className="p-2.5">Department</th>
                    <th className="p-2.5">Role</th>
                    <th className="p-2.5">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-appBorder">
                  {visibleEmployees.map((emp) => (
                    <tr key={emp.employee_id} className="hover:bg-surface-hover transition-colors">
                      <td className="p-2.5 font-medium text-[#102C54]">{emp.name}</td>
                      <td className="p-2.5 text-textSecondary font-mono text-[11px]">
                        {emp.email}
                      </td>
                      <td className="p-2.5 text-textSecondary">{emp.department}</td>
                      <td className="p-2.5 text-textSecondary">{emp.role}</td>
                      <td className="p-2.5">
                        <Badge variant="success" className="text-[10px]">
                          {emp.status || "Active"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {employees.length > 10 && (
                <div className="flex items-center justify-between pt-3 text-[11px] text-textSecondary">
                  <span>
                    Showing {visibleEmployees.length} of {employees.length} employees.
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowAll((prev) => !prev)}
                    className="text-xs h-7 px-2.5"
                  >
                    {showAll ? "Show fewer" : `Show all (${employees.length})`}
                  </Button>
                </div>
              )}
            </div>
          ) : (
            <div className="p-6 text-center text-xs text-textSecondary">
              <p className="font-semibold text-textPrimary">No employees found for this tenant.</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
