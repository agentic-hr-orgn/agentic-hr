"use client";

import React, { useState } from "react";
import Link from "next/link";
import {
  FileCheck2,
  Users2,
  TerminalSquare,
  BookOpenCheck,
  CheckCircle2,
  Mail,
  Layers,
  Database,
  ShieldCheck,
  Cpu,
  ArrowRight,
  FolderGit2,
  Settings,
  Activity,
  Server,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { sendTestEmail } from "@/lib/api-client";
import { useHealthQuery } from "@/hooks/use-health";

export default function DashboardPage() {
  const [testResult, setTestResult] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);

  // Live backend telemetry powered by TanStack Query & generated OpenAPI client
  const { data: health, isLoading: isHealthLoading, isError: isHealthError } = useHealthQuery();

  const handleTestEmail = async () => {
    setIsSending(true);
    setTestResult(null);
    try {
      const res = await sendTestEmail(
        "agentic.hr.test.ubaid.candidate1@gmail.com",
        "Stage 1 Frontend Test",
        "Simulated email sent through Stage 1 UI with USE_FAKE_INTEGRATIONS=true."
      );
      setTestResult(JSON.stringify(res, null, 2));
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setTestResult(`Error: ${message}`);
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Hero Banner */}
      <div className="hero-section rounded-large p-6 md:p-8 text-white shadow-card">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="warning" className="bg-amber-400/20 text-amber-200 border-amber-300/30">
                Stage 1 — Platform Skeleton
              </Badge>
              <Badge variant="primary" className="bg-white/20 text-white border-white/30">
                USE_FAKE_INTEGRATIONS=true
              </Badge>
              {health && (
                <Badge variant="outline" className="bg-emerald-500/20 text-emerald-200 border-emerald-400/40">
                  Backend API: {health.status}
                </Badge>
              )}
            </div>
            <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight">
              Agentic HR Core Platform
            </h1>
            <p className="text-slate-200 max-w-2xl text-xs md:text-sm leading-relaxed">
              Autonomous workforce platform for SMEs. Decoupled Next.js 16 frontend,
              FastAPI backend, multi-tenant Postgres with Row-Level Security, and least-privilege Google integrations.
            </p>
          </div>
          <div className="flex flex-col sm:flex-row gap-3">
            <Button
              variant="primary"
              onClick={handleTestEmail}
              disabled={isSending}
              className="bg-white text-navy hover:bg-slate-100 font-semibold shadow-button text-xs md:text-sm"
            >
              <Mail className="h-4 w-4 mr-2 text-primary" />
              {isSending ? "Simulating..." : "Test Fake Email"}
            </Button>
          </div>
        </div>
      </div>

      {/* TanStack Query Live Backend Telemetry */}
      {health && (
        <Card className="border-emerald-200 bg-emerald-50/30">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-emerald-600 animate-pulse" />
                <CardTitle className="text-sm font-bold text-textPrimary">
                  Live Backend Health Telemetry (TanStack Query + OpenAPI Client)
                </CardTitle>
              </div>
              <Badge variant="success">Active Query</Badge>
            </div>
            <CardDescription className="text-xs">
              Direct telemetry from FastAPI GET /health parsed via generated TypeScript OpenAPI contract.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
              <div className="p-2.5 rounded-small bg-white border border-emerald-200">
                <span className="text-textSecondary block text-[10px]">Database</span>
                <span className="font-semibold text-textPrimary">
                  {health.database_connected ? "Postgres 16 + pgvector" : "Disconnected"}
                </span>
              </div>
              <div className="p-2.5 rounded-small bg-white border border-emerald-200">
                <span className="text-textSecondary block text-[10px]">Environment</span>
                <span className="font-semibold text-textPrimary capitalize">{health.environment}</span>
              </div>
              <div className="p-2.5 rounded-small bg-white border border-emerald-200">
                <span className="text-textSecondary block text-[10px]">Strong Model</span>
                <span className="font-semibold text-textPrimary">{health.models?.strong}</span>
              </div>
              <div className="p-2.5 rounded-small bg-white border border-emerald-200">
                <span className="text-textSecondary block text-[10px]">Fast Model</span>
                <span className="font-semibold text-textPrimary">{health.models?.cheap}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Verification Output Banner */}
      {testResult && (
        <Card className="border-primary/40 bg-primary/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2 text-primary">
              <CheckCircle2 className="h-4 w-4" />
              Integration Verification Result (In-Memory Simulator):
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs bg-navy text-slate-100 p-4 rounded-small overflow-x-auto font-mono">
              {testResult}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* Quick Navigation Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Link
          href="/candidates"
          className="flex flex-col justify-between p-3.5 rounded-medium border border-appBorder bg-surface hover:border-primary/60 hover:shadow-sm transition-all"
        >
          <div className="flex items-center justify-between text-primary mb-2">
            <Users2 className="h-4 w-4" />
            <ArrowRight className="h-3.5 w-3.5 text-textSecondary" />
          </div>
          <div>
            <p className="text-xs font-bold text-textPrimary leading-none">Candidates</p>
            <p className="text-[10px] text-textSecondary mt-1">DocuVerify & Screener</p>
          </div>
        </Link>

        <Link
          href="/assessments"
          className="flex flex-col justify-between p-3.5 rounded-medium border border-appBorder bg-surface hover:border-primary/60 hover:shadow-sm transition-all"
        >
          <div className="flex items-center justify-between text-primary mb-2">
            <TerminalSquare className="h-4 w-4" />
            <ArrowRight className="h-3.5 w-3.5 text-textSecondary" />
          </div>
          <div>
            <p className="text-xs font-bold text-textPrimary leading-none">Assessments</p>
            <p className="text-[10px] text-textSecondary mt-1">E2B Sandboxes</p>
          </div>
        </Link>

        <Link
          href="/policies"
          className="flex flex-col justify-between p-3.5 rounded-medium border border-appBorder bg-surface hover:border-primary/60 hover:shadow-sm transition-all"
        >
          <div className="flex items-center justify-between text-primary mb-2">
            <BookOpenCheck className="h-4 w-4" />
            <ArrowRight className="h-3.5 w-3.5 text-textSecondary" />
          </div>
          <div>
            <p className="text-xs font-bold text-textPrimary leading-none">Policy Brain</p>
            <p className="text-[10px] text-textSecondary mt-1">Bilingual RAG</p>
          </div>
        </Link>

        <Link
          href="/employees"
          className="flex flex-col justify-between p-3.5 rounded-medium border border-appBorder bg-surface hover:border-primary/60 hover:shadow-sm transition-all"
        >
          <div className="flex items-center justify-between text-primary mb-2">
            <FolderGit2 className="h-4 w-4" />
            <ArrowRight className="h-3.5 w-3.5 text-textSecondary" />
          </div>
          <div>
            <p className="text-xs font-bold text-textPrimary leading-none">Directory</p>
            <p className="text-[10px] text-textSecondary mt-1">Sheets Synced</p>
          </div>
        </Link>

        <Link
          href="/settings"
          className="col-span-2 md:col-span-1 flex flex-col justify-between p-3.5 rounded-medium border border-appBorder bg-surface hover:border-primary/60 hover:shadow-sm transition-all"
        >
          <div className="flex items-center justify-between text-primary mb-2">
            <Settings className="h-4 w-4" />
            <ArrowRight className="h-3.5 w-3.5 text-textSecondary" />
          </div>
          <div>
            <p className="text-xs font-bold text-textPrimary leading-none">Settings</p>
            <p className="text-[10px] text-textSecondary mt-1">Tenant & Config</p>
          </div>
        </Link>
      </div>

      {/* Architectural Pillars */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-bold text-textPrimary">Frontend</CardTitle>
              <Layers className="h-4 w-4 text-primary" />
            </div>
            <CardDescription className="text-xs">Next.js 16 + TanStack Query</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-1">
            <p className="font-semibold text-textPrimary">React 18/19 + Zustand</p>
            <p>Theme: Navy Blue & Royal Blue</p>
            <p>Generated OpenAPI TypeScript Client</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-bold text-textPrimary">Backend API</CardTitle>
              <Cpu className="h-4 w-4 text-primary" />
            </div>
            <CardDescription className="text-xs">FastAPI & Python 3.13</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-1">
            <p className="font-semibold text-textPrimary">uv-managed virtualenv</p>
            <p>Temporal Cloud / Local Worker</p>
            <p>Pydantic Settings & JSON Logs</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-bold text-textPrimary">Database (ADR 002)</CardTitle>
              <Database className="h-4 w-4 text-primary" />
            </div>
            <CardDescription className="text-xs">PostgreSQL 16 + pgvector</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-1">
            <p className="font-semibold text-textPrimary">Row-Level Security (RLS)</p>
            <p>Multi-tenant: every table has tenant_id</p>
            <p>Sheets is synced view (ADR 005)</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-bold text-textPrimary">OAuth (ADR 006)</CardTitle>
              <ShieldCheck className="h-4 w-4 text-primary" />
            </div>
            <CardDescription className="text-xs">Least-Privilege Scopes</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-1">
            <p className="font-semibold text-textPrimary">6 Gmail Test Users</p>
            <p>gmail.send, drive.file, calendar</p>
            <p className="text-red-600 font-medium">No inbox-reading scopes</p>
          </CardContent>
        </Card>
      </div>

      {/* 4 Agent Archetypes */}
      <div>
        <div className="mb-4">
          <h2 className="text-base md:text-lg font-bold text-textPrimary">Core AI Workforce Archetypes</h2>
          <p className="text-xs text-textSecondary">
            Four specialized agents coordinated by the Agentic HR platform.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6">
          <Card className="hover:border-primary/50 transition-all">
            <CardHeader>
              <div className="flex items-center justify-between mb-2">
                <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                  <FileCheck2 className="h-5 w-5" />
                </div>
                <Badge variant="primary">DocuVerify</Badge>
              </div>
              <CardTitle className="text-base">Document Verifier</CardTitle>
              <CardDescription className="text-xs">
                Extracts and validates data from scanned CVs and IDs using Docling OCR.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-xs text-textSecondary">
              <p className="font-semibold text-textPrimary">Model: Gemini 3.5 Flash-Lite</p>
              <p>Supports low-quality PDFs and Urdu/English docs.</p>
            </CardContent>
          </Card>

          <Card className="hover:border-primary/50 transition-all">
            <CardHeader>
              <div className="flex items-center justify-between mb-2">
                <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                  <Users2 className="h-5 w-5" />
                </div>
                <Badge variant="primary">Screener</Badge>
              </div>
              <CardTitle className="text-base">Candidate Screener</CardTitle>
              <CardDescription className="text-xs">
                Scores candidate profiles from 0-100 against job rubrics with rubric explanations.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-xs text-textSecondary">
              <p className="font-semibold text-textPrimary">Model: Gemini 3.5 Flash-Lite</p>
              <p>Generates structured scorecard and hiring recommendations.</p>
            </CardContent>
          </Card>

          <Card className="hover:border-primary/50 transition-all">
            <CardHeader>
              <div className="flex items-center justify-between mb-2">
                <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                  <TerminalSquare className="h-5 w-5" />
                </div>
                <Badge variant="secondary">Code Tester</Badge>
              </div>
              <CardTitle className="text-base">Sandbox Evaluator</CardTitle>
              <CardDescription className="text-xs">
                Executes candidate code tests safely inside isolated E2B sandboxes.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-xs text-textSecondary">
              <p className="font-semibold text-textPrimary">Sandbox: E2B Firewalled</p>
              <p>Automated test suite execution and benchmark logging.</p>
            </CardContent>
          </Card>

          <Card className="hover:border-primary/50 transition-all">
            <CardHeader>
              <div className="flex items-center justify-between mb-2">
                <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                  <BookOpenCheck className="h-5 w-5" />
                </div>
                <Badge variant="primary">Policy Brain</Badge>
              </div>
              <CardTitle className="text-base">Bilingual RAG</CardTitle>
              <CardDescription className="text-xs">
                Answers company policy and leave inquiries in English and Urdu.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-xs text-textSecondary">
              <p className="font-semibold text-textPrimary">Model: Gemini 3.8 Flash</p>
              <p>Embeddings: Gemini Embedding 2 (Urdu + Eng).</p>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Synthetic Dataset Overview */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <span>Synthetic Dataset (Acme Tech Solutions)</span>
            <Badge variant="outline">Stage 0 Kit</Badge>
          </CardTitle>
          <CardDescription className="text-xs">
            Configured in accordance with docs/synthetic-data-plan.md and docs/adr/006-workspace-workaround.md.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
            <div className="p-3 bg-appBg rounded-small border border-appBorder">
              <span className="text-textSecondary block">Total Employees</span>
              <span className="text-base font-bold text-textPrimary">40</span>
              <span className="text-[10px] text-textSecondary block">HR(5), Eng(15), Sales(10), Ops(10)</span>
            </div>
            <div className="p-3 bg-appBg rounded-small border border-appBorder">
              <span className="text-textSecondary block">Authorized Test Users</span>
              <span className="text-base font-bold text-textPrimary">6 Accounts</span>
              <span className="text-[10px] text-textSecondary block">Gmail test users</span>
            </div>
            <div className="p-3 bg-appBg rounded-small border border-appBorder">
              <span className="text-textSecondary block">Policy Documents</span>
              <span className="text-base font-bold text-textPrimary">6 Policies</span>
              <span className="text-[10px] text-textSecondary block">English & Urdu Leave, Code of Conduct</span>
            </div>
            <div className="p-3 bg-appBg rounded-small border border-appBorder">
              <span className="text-textSecondary block">Testing CVs</span>
              <span className="text-base font-bold text-textPrimary">10 Resumes</span>
              <span className="text-[10px] text-textSecondary block">5 Scanned OCR, 5 Native Text</span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
