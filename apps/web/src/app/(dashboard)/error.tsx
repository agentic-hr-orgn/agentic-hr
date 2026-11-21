"use client";

import React from "react";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center p-4">
      <Card className="max-w-lg w-full border-rose-200 bg-white shadow-card">
        <CardHeader className="text-center space-y-2">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-rose-50 text-rose-600">
            <AlertTriangle className="h-5 w-5" aria-hidden="true" />
          </div>
          <CardTitle className="text-lg font-bold text-[#102C54]">
            Something went wrong in this view
          </CardTitle>
          <CardDescription className="text-xs">
            {error?.message || "An unexpected client error occurred while rendering this module."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-3">
          {error?.digest && (
            <p className="text-[11px] font-mono text-textMuted">Error ID: {error.digest}</p>
          )}
          <div className="flex flex-wrap items-center justify-center gap-3">
            <Button
              type="button"
              onClick={() => reset()}
              className="bg-[#2A5BDB] text-white hover:bg-[#102C54] text-xs inline-flex items-center gap-1.5"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Try Again
            </Button>
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 rounded-small border border-appBorder bg-white px-3.5 py-2 text-xs font-semibold text-[#102C54] hover:bg-slate-50"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              Return to Overview
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
