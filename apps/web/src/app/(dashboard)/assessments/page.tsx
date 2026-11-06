"use client";

import React from "react";
import Link from "next/link";
import {
  TerminalSquare,
  ShieldCheck,
  Cpu,
  Code2,
  ArrowLeft,
  CheckCircle2,
  ExternalLink,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export default function AssessmentsPage() {
  return (
    <div className="space-y-6">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-appBorder pb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              href="/"
              className="text-xs text-textSecondary hover:text-primary flex items-center gap-1 font-medium transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Dashboard
            </Link>
            <span className="text-textMuted text-xs">/</span>
            <span className="text-xs text-textPrimary font-semibold">Assessments</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-textPrimary">
            Code Tester & Sandboxed Assessments
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Isolated candidate technical assessment and test suite execution inside firewalled E2B cloud environments.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">E2B Firewalled Sandbox</Badge>
          <Badge variant="warning">Stage 1: Placeholder</Badge>
        </div>
      </div>

      {/* Agent Overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card className="md:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                <TerminalSquare className="h-5 w-5" />
              </div>
              <Badge variant="primary">Agent 3: Code Tester</Badge>
            </div>
            <CardTitle className="text-base">Autonomous Technical Assessment</CardTitle>
            <CardDescription className="text-xs">
              Automated compilation, unit testing, and static evaluation in isolated environments.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-4">
            <p>
              Candidates submit code solutions to real-world engineering challenges. The Code Tester agent
              provisions an ephemeral E2B sandbox, executes unit and integration tests, evaluates runtime
              performance, and scores code cleanliness.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
                <span className="font-semibold text-textPrimary flex items-center gap-1.5">
                  <ShieldCheck className="h-3.5 w-3.5 text-primary" />
                  Network Isolation
                </span>
                <span>Sandboxes run with egress firewalls, preventing data exfiltration or external network calls.</span>
              </div>
              <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
                <span className="font-semibold text-textPrimary flex items-center gap-1.5">
                  <Cpu className="h-3.5 w-3.5 text-primary" />
                  Deterministic Grading
                </span>
                <span>Grading is derived strictly from test suite results, not LLM speculation.</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-bold text-textPrimary">Sandbox Specifications</CardTitle>
            <CardDescription className="text-xs">E2B Infrastructure Plan</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Execution Timeout</span>
              <span className="font-semibold text-textPrimary">60 seconds per test run</span>
            </div>
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Supported Runtimes</span>
              <span className="font-semibold text-textPrimary">Python 3.12+, Node 20+, Go 1.22</span>
            </div>
            <div>
              <span className="text-textMuted block">Security Boundary</span>
              <span className="font-semibold text-textPrimary">Strict read-only filesystem mounts</span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Code Assessment Sandbox Mock */}
      <Card className="border-appBorder bg-surface">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Code2 className="h-4 w-4 text-primary" />
              <CardTitle className="text-sm font-semibold text-textPrimary">
                Synthetic Assessment Challenge: Backend Rate Limiter
              </CardTitle>
            </div>
            <Badge variant="outline">Stage 3 Preview</Badge>
          </div>
          <CardDescription className="text-xs">
            Assigned to synthetic test candidate: agentic.hr.test.ubaid.candidate1@gmail.com
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-small bg-navy text-slate-100 p-4 font-mono text-xs overflow-x-auto space-y-1">
            <p className="text-emerald-400"># E2B Sandbox Terminal Output (Simulated)</p>
            <p className="text-slate-300">$ pytest tests/test_rate_limiter.py -v</p>
            <p className="text-emerald-400">test_token_bucket_burst_allow PASSED [ 33%]</p>
            <p className="text-emerald-400">test_token_bucket_exhaustion_rejects PASSED [ 66%]</p>
            <p className="text-emerald-400">test_sliding_window_recovery PASSED [100%]</p>
            <p className="text-slate-400 pt-2">== 3 passed in 0.42s (Code Score: 96/100, Cleanliness: 92/100) ==</p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

