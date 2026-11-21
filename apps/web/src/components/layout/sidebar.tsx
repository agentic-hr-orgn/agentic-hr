"use client";

import React, { useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Users2,
  TerminalSquare,
  BookOpenCheck,
  LayoutDashboard,
  ShieldAlert,
  Settings,
  FolderGit2,
  X,
  Building2,
  CheckCircle2,
  Rocket,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useUIStore } from "@/stores/ui-store";
import { useCurrentUser } from "@/hooks/use-current-user";

interface NavItem {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  href: string;
  badge?: string;
  roles?: string[]; // optional role gating
}

const navItems: NavItem[] = [
  { label: "Overview", icon: LayoutDashboard, href: "/" },
  { label: "Onboarding Wizard", icon: Rocket, href: "/onboarding", badge: "Setup" },
  { label: "Approvals", icon: CheckCircle2, href: "/approvals", badge: "Human Gate" },
  { label: "Candidates", icon: Users2, href: "/candidates", badge: "DocuVerify & Screener" },
  { label: "Code Tester", icon: TerminalSquare, href: "/assessments", badge: "E2B Sandbox" },
  { label: "Policy Brain", icon: BookOpenCheck, href: "/policies", badge: "Bilingual RAG" },
  { label: "Directory", icon: FolderGit2, href: "/employees", badge: "Sheets View" },
  { label: "Settings", icon: Settings, href: "/settings" },
];

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname();
  const storeIsOpen = useUIStore((state) => state.isSidebarOpen);
  const setSidebarOpen = useUIStore((state) => state.setSidebarOpen);
  const { data: currentUser } = useCurrentUser();
  const { useFakeIntegrations } = useBackendStatus();

  const activeIsOpen = isOpen !== undefined ? isOpen : storeIsOpen;
  const handleClose = () => {
    if (onClose) {
      onClose();
    } else {
      setSidebarOpen(false);
    }
  };

  useEffect(() => {
    if (!activeIsOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (onClose) {
          onClose();
        } else {
          setSidebarOpen(false);
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeIsOpen, onClose, setSidebarOpen]);

  const userRole = currentUser?.role || "owner";

  return (
    <aside
      className={`fixed top-0 bottom-0 left-0 z-50 flex h-full w-64 flex-col justify-between border-r border-navy-light bg-navy text-white transition-transform duration-200 ease-in-out md:translate-x-0 ${
        activeIsOpen ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <div>
        {/* Brand & Mobile Close */}
        <div className="flex items-center justify-between border-b border-navy-light/40 px-6 py-5">
          <Link href="/" className="flex items-center gap-3" onClick={handleClose}>
            <div className="flex h-10 w-10 items-center justify-center rounded-medium bg-primary text-lg font-bold text-white shadow-button">
              A
            </div>
            <div>
              <h1 className="text-base font-bold leading-tight tracking-tight text-white">
                Agentic HR
              </h1>
              <span className="text-xs font-medium text-slate-300">Core Platform V1</span>
            </div>
          </Link>
          <button
            onClick={handleClose}
            className="rounded-small p-1 text-slate-300 hover:bg-navy-light hover:text-white md:hidden"
            aria-label="Close sidebar"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Integration Status (ADR 006) */}
        <div className="border-b border-navy-light/30 bg-navy-dark/40 px-6 py-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-300">Integrations:</span>
            <Badge
              variant={useFakeIntegrations ? "warning" : "success"}
              className="text-[10px] font-bold uppercase tracking-wider"
            >
              {useFakeIntegrations ? "Fake Mode (ADR 006)" : "Live Mode (ADR 006)"}
            </Badge>
          </div>
        </div>

        {/* Role-Aware Navigation Links */}
        <nav className="mt-4 space-y-1 px-3">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive =
              item.href === "/"
                ? pathname === "/"
                : pathname === item.href || pathname.startsWith(`${item.href}/`);

            return (
              <Link
                key={item.label}
                href={item.href}
                onClick={handleClose}
                className={`flex items-center justify-between rounded-small px-3 py-2.5 text-sm font-medium transition-all ${
                  isActive
                    ? "bg-primary text-white shadow-button"
                    : "text-slate-200 hover:bg-navy-light hover:text-white"
                }`}
              >
                <div className="flex items-center gap-3">
                  <Icon className="h-4 w-4" />
                  <span>{item.label}</span>
                </div>
                {item.badge && (
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                      isActive ? "bg-white/20 text-white" : "bg-navy-light text-slate-300"
                    }`}
                  >
                    {item.badge}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
      </div>

      {/* Footer Info: Dynamic Tenancy & Role Context */}
      <div className="border-t border-navy-light/40 bg-navy-dark/60 p-4 text-xs text-slate-400">
        <div className="mb-1 flex items-center justify-between">
          <div className="flex items-center gap-1.5 overflow-hidden">
            <Building2 className="h-3.5 w-3.5 text-primary shrink-0" />
            <span className="font-semibold text-slate-200 truncate">
              {currentUser?.tenant_name || "Acme Tech Solutions"}
            </span>
          </div>
          <span className="rounded bg-navy-light px-1.5 py-0.5 text-[10px] font-bold text-amber-300 uppercase tracking-wider">
            {userRole}
          </span>
        </div>
        <div className="flex items-center gap-1 mt-1 text-[11px] text-slate-400">
          <ShieldAlert className="h-3 w-3 text-emerald-400" />
          <span>
            {currentUser?.is_hr_head ? "HR Head" : "Staff"}
            {currentUser?.is_payroll_admin ? " • Payroll Admin" : ""}
          </span>
        </div>
      </div>
    </aside>
  );
}
