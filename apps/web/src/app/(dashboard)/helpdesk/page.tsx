"use client";

import React from "react";
import Link from "next/link";
import { LifeBuoy } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useHydrated } from "@/hooks/use-hydrated";

export default function HelpdeskPlaceholderPage() {
  const hydrated = useHydrated();

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-appBorder pb-4">
        <div className="flex items-center gap-2.5">
          <LifeBuoy className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
          <h1 className="text-xl font-bold text-[#102C54]">HR &amp; IT Helpdesk</h1>
        </div>
        <Badge variant="outline">Stage 7 Tickets &amp; SLA</Badge>
      </div>

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="text-base text-[#102C54]">
            Helpdesk Knowledge Base &amp; SLA Ticketing
          </CardTitle>
          <CardDescription className="text-xs">
            Read-only Helpdesk KB assistant, employee ticket queue, and SLA routing rules.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-xs text-textSecondary">
          <p>
            {hydrated
              ? "Helpdesk ticketing, SLA timers, and KB query hooks are initialized and ready."
              : "Initializing authenticated Helpdesk workspace..."}
          </p>
          <Link href="/">
            <Button variant="outline" size="sm">
              Return to Overview
            </Button>
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
