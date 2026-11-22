"use client";

import React from "react";
import { CalendarDays } from "lucide-react";
import type { LeaveBalanceItemResponse } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDays } from "@/components/leave/leave-status-badges";

interface LeaveBalanceCardsProps {
  balances: LeaveBalanceItemResponse[];
  isLoading: boolean;
  year: number;
}

export function LeaveBalanceCards({ balances, isLoading, year }: LeaveBalanceCardsProps) {
  if (isLoading) {
    return (
      <div
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
        aria-busy="true"
        aria-label="Loading leave balances"
      >
        {[0, 1, 2, 3].map((idx) => (
          <div
            key={idx}
            className="h-32 animate-pulse rounded-medium border border-appBorder bg-white"
          />
        ))}
      </div>
    );
  }

  if (balances.length === 0) {
    return (
      <Card className="border border-appBorder bg-white shadow-sm">
        <CardContent className="py-5 text-xs text-textSecondary flex items-center gap-2">
          <CalendarDays className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
          <span>
            No leave balance records initialized for {year} yet. Link an employee profile or submit
            a leave configuration in Settings to initialize allowances.
          </span>
        </CardContent>
      </Card>
    );
  }

  return (
    <section aria-label={`Leave balances for ${year}`}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {balances.map((bal) => (
          <Card key={bal.id} className="border border-appBorder bg-white shadow-sm">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm font-bold text-[#102C54]">
                  {bal.leave_type_name}
                </CardTitle>
                <Badge variant={bal.is_paid ? "primary" : "outline"} className="text-[10px]">
                  {bal.is_paid ? "Paid" : "Unpaid"}
                </Badge>
              </div>
              <div className="flex items-baseline justify-between pt-1">
                <span className="text-2xl font-extrabold text-[#2A5BDB]">
                  {formatDays(bal.available_days)}
                  <span className="ml-1 text-xs font-medium text-textSecondary">days avail</span>
                </span>
                <span className="text-[10px] font-mono uppercase text-textMuted">
                  {bal.accrual_mode}
                </span>
              </div>
            </CardHeader>
            <CardContent className="pt-2 border-t border-appBorder/60">
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                <div className="flex justify-between">
                  <dt className="text-textSecondary">Allocated:</dt>
                  <dd className="font-semibold text-[#102C54]">{formatDays(bal.allocated_days)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-textSecondary">Carry Fwd:</dt>
                  <dd className="font-semibold text-[#102C54]">
                    {formatDays(bal.carried_forward_days)}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-textSecondary">Reserved:</dt>
                  <dd className="font-semibold text-amber-700">{formatDays(bal.reserved_days)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-textSecondary">Used:</dt>
                  <dd className="font-semibold text-emerald-700">{formatDays(bal.used_days)}</dd>
                </div>
              </dl>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}
