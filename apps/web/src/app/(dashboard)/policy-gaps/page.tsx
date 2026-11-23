"use client";

import React from "react";
import Link from "next/link";
import { Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useHydrated } from "@/hooks/use-hydrated";

export default function PolicyGapsPlaceholderPage() {
  const hydrated = useHydrated();
  const { data: currentUser } = useCurrentUser();
  const canViewGaps =
    hydrated && (currentUser?.role === "owner" || Boolean(currentUser?.is_hr_head));

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-appBorder pb-4">
        <div className="flex items-center gap-2.5">
          <Sparkles className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
          <h1 className="text-xl font-bold text-[#102C54]">HR Policy Gaps</h1>
        </div>
        <Badge variant="outline">Owner &amp; HR Head Only</Badge>
      </div>

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="text-base text-[#102C54]">
            Unanswered &amp; Down-Voted Policy Topics
          </CardTitle>
          <CardDescription className="text-xs">
            Tracks abstained Policy Brain questions and negative employee feedback grouped by topic.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-xs text-textSecondary">
          <p>
            {!hydrated
              ? "Checking organization role permissions..."
              : canViewGaps
                ? "Policy Gaps aggregation hooks are ready for HR review."
                : "Policy Gaps analytics are restricted to Organization Owners and HR Heads."}
          </p>
          <Link href="/policies">
            <Button variant="outline" size="sm">
              Open Policy Library
            </Button>
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
