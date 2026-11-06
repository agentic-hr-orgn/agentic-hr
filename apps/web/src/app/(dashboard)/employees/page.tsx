"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  FolderGit2,
  Users,
  FileSpreadsheet,
  ArrowLeft,
  CheckCircle2,
  RefreshCw,
  Database,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { fetchEmployees, type Employee } from "@/lib/api-client";

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadEmployees = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchEmployees();
      setEmployees(data);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadEmployees();
  }, []);

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
          <h1 className="text-2xl font-bold tracking-tight text-textPrimary">
            Employee Directory & Sheets Synchronization
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            PostgreSQL 16 is authoritative; Google Sheets serves as a designated synced view (ADR 002 & ADR 005).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="primary">Postgres Authoritative</Badge>
          <Badge variant="warning">Sheets Synced View</Badge>
        </div>
      </div>

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
            <CardTitle className="text-base">Durable Multi-Day Syncing Architecture</CardTitle>
            <CardDescription className="text-xs">
              Temporal workflows coordinate two-way reconciliation between Postgres and Google Sheets.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <p>
              To ensure reliability without data corruption, PostgreSQL maintains authoritative ownership of employee,
              leave balance, and payroll state. Business users can view and edit designated ranges in Google Sheets, which
              are safely synchronized via Temporal durable activities under least-privilege scopes.
            </p>
            <div className="p-3 bg-appBg rounded-small border border-appBorder">
              <span className="font-semibold text-textPrimary block mb-1">Synthetic Dataset Profile:</span>
              <span>40 Acme Tech Solutions employees: HR (5), Engineering (15), Sales (10), Operations (10).</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-bold text-textPrimary">Sync Controls</CardTitle>
            <CardDescription className="text-xs">In-Memory Simulator</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button
              variant="outline"
              size="sm"
              onClick={loadEmployees}
              disabled={isLoading}
              className="w-full text-xs"
            >
              <RefreshCw className={`h-3.5 w-3.5 mr-2 ${isLoading ? "animate-spin" : ""}`} />
              {isLoading ? "Refreshing..." : "Reload Postgres Data"}
            </Button>
            <div className="text-[11px] text-textSecondary space-y-1">
              <p>• Tenant: acme-tech-01</p>
              <p>• Mode: USE_FAKE_INTEGRATIONS=true</p>
              <p>• Sync Status: Idle (Temporal Worker)</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Employee List / Table Preview */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <Users className="h-4 w-4 text-primary" />
              <span>Employees ({employees.length > 0 ? employees.length : "40 Synthetic"})</span>
            </CardTitle>
            <Badge variant="outline">Stage 1 Synthetic Feed</Badge>
          </div>
          <CardDescription className="text-xs">
            Live preview loaded from FastAPI backend synthetic dataset endpoint.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <div className="p-3 rounded-small bg-amber-50 border border-amber-200 text-xs text-amber-800 mb-4">
              FastAPI Standby: {error}. Backend synthetic endpoints can be connected via local dev server.
            </div>
          )}

          {employees.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-appBorder bg-appBg text-textSecondary font-semibold">
                    <th className="p-2.5">Name</th>
                    <th className="p-2.5">Email</th>
                    <th className="p-2.5">Department</th>
                    <th className="p-2.5">Role</th>
                    <th className="p-2.5">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-appBorder">
                  {employees.slice(0, 10).map((emp) => (
                    <tr key={emp.employee_id} className="hover:bg-surface-hover transition-colors">
                      <td className="p-2.5 font-medium text-textPrimary">{emp.name}</td>
                      <td className="p-2.5 text-textSecondary font-mono text-[11px]">{emp.email}</td>
                      <td className="p-2.5 text-textSecondary">{emp.department}</td>
                      <td className="p-2.5 text-textSecondary">{emp.role}</td>
                      <td className="p-2.5">
                        <Badge variant="success" className="text-[10px]">
                          Active
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {employees.length > 10 && (
                <p className="text-[11px] text-textSecondary text-center pt-3">
                  Showing first 10 of {employees.length} synthetic employees.
                </p>
              )}
            </div>
          ) : (
            <div className="p-6 text-center text-xs text-textSecondary">
              <p className="font-semibold text-textPrimary">40 Synthetic Employees Ready</p>
              <p className="mt-1">
                Engineering (15), Sales (10), Operations (10), HR (5) configured in accordance with ADR 006.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
