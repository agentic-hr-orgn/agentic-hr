"use client";

import React from "react";
import { usePathname } from "next/navigation";
import { Activity, ShieldCheck, Menu, Building2 } from "lucide-react";
import { OrganizationSwitcher, UserButton, useAuth } from "@clerk/nextjs";
import { Badge } from "@/components/ui/badge";
import { NotificationBell } from "@/components/layout/notification-bell";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useHydrated } from "@/hooks/use-hydrated";
import { getApiHostLabel, PLATFORM_STAGE } from "@/lib/platform";
import { useUIStore } from "@/stores/ui-store";

interface HeaderProps {
  onMenuClick?: () => void;
}

const routeTitles: Array<{ prefix: string; title: string; subtitle: string }> = [
  {
    prefix: "/onboarding",
    title: "Onboarding Wizard",
    subtitle: "5-Step Workspace Setup & Launch",
  },
  {
    prefix: "/approvals/",
    title: "Approval Correction",
    subtitle: "Revise & Resubmit Returned Governance Draft",
  },
  {
    prefix: "/approvals",
    title: "Approvals Inbox",
    subtitle: "Human-in-the-Loop Governance Gate",
  },
  {
    prefix: "/candidates",
    title: "Candidate Management",
    subtitle: "DocuVerify OCR & Rubric Screener",
  },
  {
    prefix: "/assessments",
    title: "Technical Assessments",
    subtitle: "E2B Sandboxed Code Tester",
  },
  {
    prefix: "/policies/",
    title: "Policy Preview & Fix",
    subtitle: "Inspect Parsed Chunks, Repair OCR & Submit for Publish",
  },
  {
    prefix: "/policies",
    title: "Corporate Policy Brain",
    subtitle: "Policy Library, Bilingual Search & OCR Review",
  },
  {
    prefix: "/chat",
    title: "Policy Brain Chat",
    subtitle: "Grounded Bilingual Policy QA & Tools",
  },
  {
    prefix: "/policy-gaps",
    title: "HR Policy Gaps",
    subtitle: "Abstained & Down-Voted Policy Topics",
  },
  {
    prefix: "/helpdesk",
    title: "HR & IT Helpdesk",
    subtitle: "Read-Only KB Assistant, Tickets & SLA",
  },
  {
    prefix: "/leave",
    title: "Leave Management",
    subtitle: "Balances, AI Intake, Approvals & Team Calendar",
  },
  {
    prefix: "/attendance",
    title: "Attendance & Month Lock",
    subtitle: "Check-In, Daily Grid, Bulk Import & Payroll Lock",
  },
  {
    prefix: "/employees",
    title: "Employee Directory",
    subtitle: "Postgres Authoritative, Sheets Synced",
  },
  {
    prefix: "/directory",
    title: "Employee Directory",
    subtitle: "Postgres Authoritative, Sheets Synced",
  },
  {
    prefix: "/settings",
    title: "Platform Settings",
    subtitle: "Multi-Tenant & Integration Boundary",
  },
  {
    prefix: "/",
    title: "Dashboard Overview",
    subtitle: "Autonomous SME Workforce Hub",
  },
];

export function resolveRouteTitle(pathname: string): { title: string; subtitle: string } {
  const normalized = pathname || "/";
  if (normalized === "/") {
    return { title: "Dashboard Overview", subtitle: "Autonomous SME Workforce Hub" };
  }
  const match = routeTitles.find(
    (entry) => entry.prefix !== "/" && normalized.startsWith(entry.prefix)
  );
  return (
    match || {
      title: "Platform Core",
      subtitle: "Agentic HR Core",
    }
  );
}

export function Header({ onMenuClick }: HeaderProps) {
  const hydrated = useHydrated();
  const pathname = usePathname();
  const toggleSidebar = useUIStore((state) => state.toggleSidebar);
  const { isLoaded, isSignedIn } = useAuth();
  const showClerkSession = hydrated && isLoaded && isSignedIn;

  // FastAPI backend health status
  const { status: backendStatus } = useBackendStatus();
  const apiHost = getApiHostLabel();

  // Authoritative verified user context from FastAPI /api/v1/auth/me
  const { data: currentUser } = useCurrentUser();

  const currentRoute = resolveRouteTitle(pathname);

  const handleMenuClick = onMenuClick || toggleSidebar;

  const userDisplayName =
    currentUser?.name || currentUser?.email?.split("@")[0] || "Authorized User";
  const tenantDisplayName = currentUser?.tenant_name || "Workspace";
  const userRole = currentUser?.role ? currentUser.role.toUpperCase() : "—";

  return (
    <header className="sticky top-0 z-30 flex min-h-16 py-2 w-full items-center justify-between gap-2 border-b border-appBorder bg-surface px-4 md:px-8 shadow-sm">
      <div className="flex items-center gap-3 min-w-0">
        {/* Mobile menu trigger using Zustand */}
        <button
          type="button"
          onClick={handleMenuClick}
          className="rounded-small p-2 text-textSecondary hover:bg-surface-hover hover:text-textPrimary md:hidden shrink-0"
          aria-label="Open sidebar navigation"
        >
          <Menu className="h-5 w-5" />
        </button>

        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm md:text-base font-bold text-textPrimary leading-tight truncate">
              {currentRoute.title}
            </h2>
            <Badge
              variant="primary"
              className="hidden sm:inline-flex text-[11px] whitespace-nowrap shrink-0"
            >
              {PLATFORM_STAGE}
            </Badge>
          </div>
          <p className="text-[11px] text-textSecondary hidden sm:block truncate">
            {currentRoute.subtitle}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2.5 md:gap-4 shrink-0">
        {/* Clerk Organization Switcher */}
        {showClerkSession ? (
          <div className="hidden sm:block shrink-0">
            <OrganizationSwitcher
              hidePersonal={true}
              afterSelectOrganizationUrl="/"
              afterCreateOrganizationUrl="/"
              appearance={{
                elements: {
                  rootBox: "flex items-center",
                  organizationSwitcherTrigger:
                    "border border-appBorder rounded-medium px-2.5 py-1 text-xs font-semibold hover:bg-surface-hover text-textPrimary whitespace-nowrap",
                },
              }}
            />
          </div>
        ) : (
          <div className="hidden sm:flex items-center gap-1.5 border border-appBorder rounded-small px-2.5 py-1 text-xs text-textSecondary bg-appBg whitespace-nowrap shrink-0">
            <Building2 className="h-3.5 w-3.5 text-primary shrink-0" />
            <span className="font-medium text-textPrimary">{tenantDisplayName}</span>
          </div>
        )}

        {/* Backend Status indicator */}
        <div
          className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 border text-xs font-medium transition-colors whitespace-nowrap shrink-0 ${
            backendStatus === "live"
              ? "bg-emerald-50 text-emerald-800 border-emerald-200"
              : backendStatus === "standby"
                ? "bg-amber-50 text-amber-800 border-amber-200"
                : "bg-slate-50 text-slate-700 border-slate-200"
          }`}
        >
          <Activity
            className={`h-3 w-3 shrink-0 ${
              backendStatus === "live"
                ? "text-emerald-600 animate-pulse"
                : backendStatus === "standby"
                  ? "text-amber-600"
                  : "text-slate-400"
            }`}
          />
          <span className="hidden xl:inline">FastAPI:</span>
          <span>
            {backendStatus === "live"
              ? `Online (${apiHost})`
              : backendStatus === "standby"
                ? "Standby"
                : "Querying..."}
          </span>
        </div>

        {/* Multi-Tenant Security indicator */}
        <div className="hidden xl:flex items-center gap-1.5 text-xs text-textSecondary border border-appBorder rounded-small px-2.5 py-1 bg-appBg whitespace-nowrap shrink-0">
          <ShieldCheck className="h-3.5 w-3.5 text-primary shrink-0" />
          <span>Tenant Isolated (ADR 002)</span>
        </div>

        {/* Global Notification Bell */}
        <NotificationBell />

        {/* User Account / Profile */}
        <div className="flex items-center gap-2 pl-2 border-l border-appBorder shrink-0">
          {showClerkSession ? (
            <div className="flex items-center gap-2">
              <UserButton afterSignOutUrl="/sign-in" />
              <div className="hidden md:block text-left text-xs leading-tight">
                <p className="font-semibold text-textPrimary whitespace-nowrap">
                  {userDisplayName}
                </p>
                <p className="text-[10px] text-textSecondary mt-0.5 font-medium whitespace-nowrap">
                  {userRole} • {tenantDisplayName}
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="h-8 w-8 rounded-full bg-navy flex items-center justify-center text-white text-xs font-bold shadow-sm shrink-0">
                {userDisplayName.slice(0, 2).toUpperCase()}
              </div>
              <div className="hidden md:block text-left text-xs leading-tight">
                <p className="font-semibold text-textPrimary whitespace-nowrap">
                  {userDisplayName}
                </p>
                <p className="text-[10px] text-textSecondary mt-0.5 whitespace-nowrap">
                  {userRole} ({currentUser?.clerk_org_id || "acme-tech-01"})
                </p>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
