"use client";

import React from "react";
import Link from "next/link";
import { MessageSquareText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useHydrated } from "@/hooks/use-hydrated";

export default function PolicyChatPlaceholderPage() {
  const hydrated = useHydrated();

  return (
    <div className="space-y-6 bg-[#F8F9FD] pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-appBorder pb-4">
        <div className="flex items-center gap-2.5">
          <MessageSquareText className="h-5 w-5 text-[#2A5BDB]" aria-hidden="true" />
          <h1 className="text-xl font-bold text-[#102C54]">Policy Brain Chat</h1>
        </div>
        <Badge variant="outline">Stage 7 Grounded QA</Badge>
      </div>

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="text-base text-[#102C54]">
            Bilingual Policy Brain Workspace
          </CardTitle>
          <CardDescription className="text-xs">
            Interactive Policy Brain streaming chat, verified policy citations, and leave handoffs.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-xs text-textSecondary">
          <p>
            {hydrated
              ? "Policy Brain session streaming and bilingual RAG hooks are initialized and ready."
              : "Initializing authenticated Policy Brain workspace..."}
          </p>
          <Link href="/policies">
            <Button variant="outline" size="sm">
              Browse Published Policies
            </Button>
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
