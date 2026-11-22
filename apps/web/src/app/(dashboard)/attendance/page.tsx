"use client";

import React from "react";
import Link from "next/link";
import { ArrowLeft, CalendarCheck2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function AttendancePlaceholderPage() {
  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
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
            <span className="text-xs text-[#102C54] font-semibold">Attendance</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[#102C54]">
            Attendance &amp; Month Lock
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Daily check-in, manager attendance grid, two-phase Excel import, and payroll month lock.
          </p>
        </div>
        <Badge variant="primary">Stage 6 Attendance</Badge>
      </div>

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CalendarCheck2 className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
            <CardTitle className="text-base text-[#102C54]">
              Attendance Workspace Initializing
            </CardTitle>
          </div>
          <CardDescription className="text-xs text-textSecondary">
            Backend Attendance APIs, two-phase Excel import, and month-lock governance are live.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-xs text-textSecondary space-y-2">
          <p>
            Configure work-week days, self check-in toggle, and holidays in{" "}
            <Link href="/settings" className="font-semibold text-[#2A5BDB] hover:underline">
              Settings
            </Link>{" "}
            or manage team leaves in{" "}
            <Link href="/leave" className="font-semibold text-[#2A5BDB] hover:underline">
              Leave Management
            </Link>
            .
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
