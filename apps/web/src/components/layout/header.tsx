import React from "react";
import { Sparkles, Activity, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export function Header() {
  return (
    <header className="sticky top-0 z-30 flex h-16 w-full items-center justify-between border-b border-appBorder bg-surface px-8 shadow-sm">
      <div className="flex items-center gap-3">
        <h2 className="text-base font-semibold text-textPrimary">
          Stage 1: Platform Skeleton
        </h2>
        <Badge variant="primary" className="text-xs">
          Stage 1 Active
        </Badge>
      </div>

      <div className="flex items-center gap-4">
        {/* Backend Status indicator */}
        <div className="flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1 border border-emerald-200 text-xs font-medium text-emerald-800">
          <Activity className="h-3.5 w-3.5 text-emerald-600 animate-pulse" />
          <span>FastAPI: Online (Port 8000)</span>
        </div>

        {/* Security / Isolation pill */}
        <div className="hidden sm:flex items-center gap-1.5 text-xs text-textSecondary border border-appBorder rounded-small px-2.5 py-1 bg-appBg">
          <ShieldCheck className="h-3.5 w-3.5 text-primary" />
          <span>RLS Enforced (ADR 002)</span>
        </div>

        {/* User identifier */}
        <div className="flex items-center gap-2 pl-2 border-l border-appBorder">
          <div className="h-8 w-8 rounded-full bg-navy flex items-center justify-center text-white text-xs font-bold">
            UQ
          </div>
          <div className="hidden md:block text-left text-xs">
            <p className="font-semibold text-textPrimary leading-none">Ubaid Qureshi</p>
            <p className="text-[10px] text-textSecondary">Owner (acme-tech-01)</p>
          </div>
        </div>
      </div>
    </header>
  );
}

