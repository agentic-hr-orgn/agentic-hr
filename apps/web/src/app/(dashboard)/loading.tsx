import React from "react";

export default function DashboardLoading() {
  return (
    <div role="status" aria-label="Loading workspace content" className="space-y-6 animate-pulse">
      <div className="h-24 rounded-large bg-[#102C54]/10" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((idx) => (
          <div key={idx} className="h-28 rounded-medium border border-appBorder bg-white p-4">
            <div className="h-3 w-24 rounded bg-slate-200 mb-3" />
            <div className="h-6 w-16 rounded bg-[#2A5BDB]/20 mb-2" />
            <div className="h-2.5 w-32 rounded bg-slate-100" />
          </div>
        ))}
      </div>
      <div className="h-64 rounded-medium border border-appBorder bg-white p-6 space-y-3">
        <div className="h-4 w-48 rounded bg-slate-200" />
        <div className="h-3 w-full rounded bg-slate-100" />
        <div className="h-3 w-5/6 rounded bg-slate-100" />
        <div className="h-3 w-4/6 rounded bg-slate-100" />
      </div>
    </div>
  );
}
