"use client";

import React from "react";
import { usePathname } from "next/navigation";
import { Activity, ShieldCheck, Menu, Building2 } from "lucide-react";
import { OrganizationSwitcher, UserButton, useAuth } from "@clerk/nextjs";
import { Badge } from "@/components/ui/badge";
import { useHealthQuery } from "@/hooks/use-health";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useUIStore } from "@/stores/ui-store";

interface HeaderProps {
  onMenuClick?: () => void;
}

const routeTitles: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Dashboard Overview", subtitle: "Autonomous SME Workforce Hub" },
  "/candidates": { title: "Candidate Management", subtitle: "DocuVerify OCR & Rubric Screener" },
  "/assessments": { title: "Technical Assessments", subtitle: "E2B Sandboxed Code Tester" },
  "/policies": { title: "Corporate Policy Brain", subtitle: "Bilingual English & Urdu RAG" },
  "/employees": { title: "Employee Directory", subtitle: "Postgres Authoritative, Sheets Synced" },
  "/settings": { title: "Platform Settings", subtitle: "Multi-Tenant & Integration Boundary" },
};

export function Header({ onMenuClick }: HeaderProps) {
  const pathname = usePathname();
  const toggleSidebar = useUIStore((state) => state.toggleSidebar);
  const { isLoaded, isSignedIn } = useAuth();

  // FastAPI backend health query
  const { data: health, isError } = useHealthQuery();
  const apiOnline = health
    ? health.status === "healthy" || health.status === "ok"
    : isError
      ? false
      : null;

  // Authoritative verified user context from FastAPI /api/v1/auth/me
  const { data: currentUser } = useCurrentUser();

  const currentRoute = routeTitles[pathname] || {
    title: "Platform Core",
    subtitle: "Agentic HR Core",
  };

  const handleMenuClick = onMenuClick || toggleSidebar;

  const userDisplayName =
    currentUser?.name || currentUser?.email?.split("@")[0] || "Authorized User";
  const tenantDisplayName = currentUser?.tenant_name || "Acme Tech Solutions";
  const userRole = currentUser?.role ? currentUser.role.toUpperCase() : "OWNER";

  return (
    <header className="sticky top-0 z-30 flex h-16 w-full items-center justify-between border-b border-appBorder bg-surface px-4 md:px-8 shadow-sm">
      <div className="flex items-center gap-3">
        {/* Mobile menu trigger using Zustand */}
        <button
          type="button"
          onClick={handleMenuClick}
          className="rounded-small p-2 text-textSecondary hover:bg-surface-hover hover:text-textPrimary md:hidden"
          aria-label="Open sidebar navigation"
        >
          <Menu className="h-5 w-5" />
        </button>

        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm md:text-base font-bold text-textPrimary leading-tight">
              {currentRoute.title}
            </h2>
            <Badge variant="primary" className="hidden sm:inline-flex text-[11px]">
              Stage 2
            </Badge>
          </div>
          <p className="text-[11px] text-textSecondary hidden sm:block">{currentRoute.subtitle}</p>
        </div>
      </div>

      <div className="flex items-center gap-3 md:gap-4">
        {/* Clerk Organization Switcher */}
        {isLoaded && isSignedIn ? (
          <div className="hidden sm:block">
            <OrganizationSwitcher
              hidePersonal={true}
              afterSelectOrganizationUrl="/"
              appearance={{
                elements: {
                  rootBox: "flex items-center",
                  organizationSwitcherTrigger:
                    "border border-appBorder rounded-medium px-2.5 py-1 text-xs font-semibold hover:bg-surface-hover text-textPrimary",
                },
              }}
            />
          </div>
        ) : (
          <div className="hidden sm:flex items-center gap-1.5 border border-appBorder rounded-small px-2.5 py-1 text-xs text-textSecondary bg-appBg">
            <Building2 className="h-3.5 w-3.5 text-primary" />
            <span className="font-medium text-textPrimary">{tenantDisplayName}</span>
          </div>
        )}

        {/* Backend Status indicator */}
        <div
          className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 border text-xs font-medium transition-colors ${
            apiOnline === true
              ? "bg-emerald-50 text-emerald-800 border-emerald-200"
              : apiOnline === false
                ? "bg-amber-50 text-amber-800 border-amber-200"
                : "bg-slate-50 text-slate-700 border-slate-200"
          }`}
        >
          <Activity
            className={`h-3 w-3 ${
              apiOnline === true
                ? "text-emerald-600 animate-pulse"
                : apiOnline === false
                  ? "text-amber-600"
                  : "text-slate-400"
            }`}
          />
          <span className="hidden sm:inline">FastAPI:</span>
          <span>
            {apiOnline === true ? "Online (8000)" : apiOnline === false ? "Standby" : "Querying..."}
          </span>
        </div>

        {/* Multi-Tenant Security indicator */}
        <div className="hidden lg:flex items-center gap-1.5 text-xs text-textSecondary border border-appBorder rounded-small px-2.5 py-1 bg-appBg">
          <ShieldCheck className="h-3.5 w-3.5 text-primary" />
          <span>Tenant Isolated (ADR 002)</span>
        </div>

        {/* User Account / Profile */}
        <div className="flex items-center gap-2 pl-2 border-l border-appBorder">
          {isLoaded && isSignedIn ? (
            <div className="flex items-center gap-2">
              <UserButton afterSignOutUrl="/sign-in" />
              <div className="hidden md:block text-left text-xs">
                <p className="font-semibold text-textPrimary leading-none">{userDisplayName}</p>
                <p className="text-[10px] text-textSecondary mt-0.5 font-medium">
                  {userRole} • {tenantDisplayName}
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="h-8 w-8 rounded-full bg-navy flex items-center justify-center text-white text-xs font-bold shadow-sm">
                {userDisplayName.slice(0, 2).toUpperCase()}
              </div>
              <div className="hidden md:block text-left text-xs">
                <p className="font-semibold text-textPrimary leading-none">{userDisplayName}</p>
                <p className="text-[10px] text-textSecondary mt-0.5">
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
