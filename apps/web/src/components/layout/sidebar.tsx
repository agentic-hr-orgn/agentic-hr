import React from "react";
import {
  FileCheck2,
  Users2,
  TerminalSquare,
  BookOpenCheck,
  LayoutDashboard,
  ShieldAlert,
  Settings,
  FolderGit2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";

interface NavItem {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  href: string;
  badge?: string;
  active?: boolean;
}

const navItems: NavItem[] = [
  { label: "Overview", icon: LayoutDashboard, href: "/", active: true },
  { label: "DocuVerify", icon: FileCheck2, href: "#docuverify", badge: "Agent" },
  { label: "Screener (0-100)", icon: Users2, href: "#screener", badge: "Agent" },
  { label: "Code Tester", icon: TerminalSquare, href: "#sandbox", badge: "E2B" },
  { label: "Policy Brain", icon: BookOpenCheck, href: "#brain", badge: "RAG" },
  { label: "Directory", icon: FolderGit2, href: "#directory", badge: "Sheets View" },
  { label: "Settings", icon: Settings, href: "#settings" },
];

export function Sidebar() {
  return (
    <aside className="fixed left-0 top-0 z-40 h-screen w-64 border-r border-navy-light bg-navy text-white flex flex-col justify-between">
      <div>
        {/* Brand */}
        <div className="flex items-center gap-3 px-6 py-5 border-b border-navy-light/40">
          <div className="flex h-10 w-10 items-center justify-center rounded-medium bg-primary text-white font-bold shadow-button text-lg">
            A
          </div>
          <div>
            <h1 className="text-base font-bold tracking-tight text-white leading-tight">
              Agentic HR
            </h1>
            <span className="text-xs text-slate-300 font-medium">Core Platform V1</span>
          </div>
        </div>

        {/* Integration Status (ADR 006) */}
        <div className="px-6 py-3 border-b border-navy-light/30 bg-navy-dark/40">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-300">Integrations:</span>
            <Badge variant="warning" className="text-[10px] uppercase font-bold tracking-wider">
              Fake Mode (ADR 006)
            </Badge>
          </div>
        </div>

        {/* Navigation Links */}
        <nav className="mt-4 px-3 space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <a
                key={item.label}
                href={item.href}
                className={`flex items-center justify-between px-3 py-2.5 rounded-small text-sm font-medium transition-all ${
                  item.active
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
                    className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
                      item.active
                        ? "bg-white/20 text-white"
                        : "bg-navy-light text-slate-300"
                    }`}
                  >
                    {item.badge}
                  </span>
                )}
              </a>
            );
          })}
        </nav>
      </div>

      {/* Footer Info */}
      <div className="p-4 border-t border-navy-light/40 bg-navy-dark/60 text-xs text-slate-400">
        <div className="flex items-center gap-2 mb-1">
          <ShieldAlert className="h-3.5 w-3.5 text-amber-400" />
          <span className="text-slate-300 font-semibold">Tenant: acme-tech-01</span>
        </div>
        <p className="text-[11px] text-slate-400">
          Google OAuth: 6 Test Users (No inbox-read scopes)
        </p>
      </div>
    </aside>
  );
}

