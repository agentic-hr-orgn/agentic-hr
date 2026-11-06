"use client";

import React from "react";
import Link from "next/link";
import {
  Settings,
  ShieldCheck,
  Mail,
  KeyRound,
  ArrowLeft,
  Server,
  Cpu,
  Lock,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function SettingsPage() {
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
            <span className="text-xs text-textPrimary font-semibold">Settings</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-textPrimary">
            Platform Settings & Integrations
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Multi-tenant context isolation, Google Workspace least-privilege connectors, and AI model routing.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="primary">acme-tech-01</Badge>
          <Badge variant="warning">Fake Mode Active</Badge>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Tenant Configuration */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <Badge variant="primary">Postgres RLS (ADR 002)</Badge>
            </div>
            <CardTitle className="text-base">Tenant Context & Isolation</CardTitle>
            <CardDescription className="text-xs">
              Row-Level Security boundary configured per database session.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Active Tenant ID</span>
              <span className="font-semibold text-textPrimary font-mono">acme-tech-01</span>
            </div>
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Company Name</span>
              <span className="font-semibold text-textPrimary">Acme Tech Solutions</span>
            </div>
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Tenant Boundary Implementation</span>
              <span className="font-semibold text-textPrimary font-mono text-[11px]">
                SELECT set_config(&apos;app.current_tenant_id&apos;, :tenant_id, true)
              </span>
            </div>
            <div>
              <span className="text-textMuted block">Isolation Guarantee</span>
              <span className="text-textPrimary">
                Transactions automatically limit visibility to records where tenant_id matches the session context.
              </span>
            </div>
          </CardContent>
        </Card>

        {/* Integration Switch & Scopes */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                <Mail className="h-5 w-5" />
              </div>
              <Badge variant="warning">USE_FAKE_INTEGRATIONS=true</Badge>
            </div>
            <CardTitle className="text-base">Google Workspace Integration (ADR 006)</CardTitle>
            <CardDescription className="text-xs">
              Least-privilege OAuth scopes and fake mode adapter status.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Current Adapter</span>
              <span className="font-semibold text-textPrimary">In-Memory Simulator (Zero live API calls)</span>
            </div>
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Permitted Scopes Whitelist</span>
              <span className="font-semibold text-textPrimary font-mono text-[11px]">
                gmail.send, drive.file, calendar.events
              </span>
            </div>
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Prohibited Scopes</span>
              <span className="font-semibold text-red-600 font-mono text-[11px]">
                gmail.readonly (STRICTLY PROHIBITED)
              </span>
            </div>
            <div>
              <span className="text-textMuted block">Testing Accounts Whitelist</span>
              <span className="text-textPrimary">
                6 Gmail accounts configured under Google Auth Platform.
              </span>
            </div>
          </CardContent>
        </Card>

        {/* AI Model Routing Matrix */}
        <Card className="md:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                <Cpu className="h-5 w-5" />
              </div>
              <Badge variant="outline">Google AI Studio Free Tier</Badge>
            </div>
            <CardTitle className="text-base">AI Model Routing Matrix (ADR 003)</CardTitle>
            <CardDescription className="text-xs">
              Deterministic routing matrix configured with synthetic test boundaries.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
              <div className="p-3 bg-appBg rounded-small border border-appBorder">
                <span className="text-textMuted block">Strong Reasoning Model</span>
                <span className="font-bold text-textPrimary text-sm block mt-0.5">Gemini 3.8 Flash</span>
                <span className="text-[11px] text-textSecondary block mt-1">
                  Assigned to Policy Brain RAG & complex candidate matching.
                </span>
              </div>
              <div className="p-3 bg-appBg rounded-small border border-appBorder">
                <span className="text-textMuted block">High-Throughput Model</span>
                <span className="font-bold text-textPrimary text-sm block mt-0.5">Gemini 3.5 Flash-Lite</span>
                <span className="text-[11px] text-textSecondary block mt-1">
                  Assigned to CV OCR validation & Screener scoring.
                </span>
              </div>
              <div className="p-3 bg-appBg rounded-small border border-appBorder">
                <span className="text-textMuted block">Multilingual Embeddings</span>
                <span className="font-bold text-textPrimary text-sm block mt-0.5">Gemini Embedding 2</span>
                <span className="text-[11px] text-textSecondary block mt-1">
                  Urdu & English bilingual vector embeddings for policy RAG.
                </span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

