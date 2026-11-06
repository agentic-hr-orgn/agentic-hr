"use client";

import React, { useState } from "react";
import {
  FileCheck2,
  Users2,
  TerminalSquare,
  BookOpenCheck,
  CheckCircle2,
  Mail,
  Calendar,
  Layers,
  Database,
  ShieldCheck,
  Cpu,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { sendTestEmail } from "@/lib/api-client";

export default function DashboardPage() {
  const [testResult, setTestResult] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);

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
    } catch (err: any) {
      setTestResult(`Error: ${err.message}`);
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div className="space-y-8 max-w-7xl mx-auto">
      {/* Hero Banner */}
      <div className="hero-section rounded-large p-8 text-white shadow-card">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant="warning" className="bg-amber-400/20 text-amber-200 border-amber-300/30">
                Stage 1 — Platform Skeleton
              </Badge>
              <Badge variant="primary" className="bg-white/20 text-white border-white/30">
                USE_FAKE_INTEGRATIONS=true
              </Badge>
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight">
              Agentic HR Core Platform
            </h1>
            <p className="text-slate-200 max-w-2xl text-sm leading-relaxed">
              Autonomous workforce platform for SMEs. Decoupled Next.js 14 frontend,
              FastAPI backend, multi-tenant Postgres with Row-Level Security, and least-privilege Google integrations.
            </p>
          </div>
          <div className="flex flex-col sm:flex-row gap-3">
            <Button
              variant="primary"
              onClick={handleTestEmail}
              disabled={isSending}
              className="bg-white text-navy hover:bg-slate-100 font-semibold shadow-button"
            >
              <Mail className="h-4 w-4 mr-2 text-primary" />
              {isSending ? "Simulating..." : "Test Fake Email"}
            </Button>
          </div>
        </div>
      </div>

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

      {/* Architectural Pillars */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-bold text-textPrimary">Frontend</CardTitle>
              <Layers className="h-4 w-4 text-primary" />
            </div>
            <CardDescription className="text-xs">Next.js 16 (Active LTS)</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-1">
            <p className="font-semibold text-textPrimary">React 18/19 + Tailwind</p>
            <p>Theme: Navy Blue & Royal Blue</p>
            <p>Clerk Authentication Ready</p>
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
            <p>LiteLLM Gateway Routing</p>
            <p>Langfuse & Sentry Tracing</p>
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
          <h2 className="text-lg font-bold text-textPrimary">Core AI Workforce Archetypes</h2>
          <p className="text-xs text-textSecondary">
            Four specialized agents coordinated by the Agentic HR platform.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
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
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
            <div className="p-3 bg-appBg rounded-small border border-appBorder">
              <span className="text-textSecondary block">Total Employees</span>
              <span className="text-base font-bold text-textPrimary">40</span>
              <span className="text-[10px] text-textSecondary block">HR(5), Eng(15), Sales(10), Ops(10)</span>
            </div>
            <div className="p-3 bg-appBg rounded-small border border-appBorder">
              <span className="text-textSecondary block">Authorized Test Users</span>
              <span className="text-base font-bold text-textPrimary">6 Accounts</span>
              <span className="text-[10px] text-textSecondary block">Gmail plus-aliases</span>
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

