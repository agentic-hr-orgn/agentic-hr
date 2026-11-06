"use client";

import React from "react";
import Link from "next/link";
import {
  BookOpenCheck,
  Languages,
  Search,
  FileText,
  ArrowLeft,
  Sparkles,
  ShieldCheck,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export default function PoliciesPage() {
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
            <span className="text-xs text-textPrimary font-semibold">Policies</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-textPrimary">
            Corporate Policy Brain (Bilingual RAG)
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            Multilingual knowledge retrieval assistant in English and Urdu for leaves, code of
            conduct, and HR policies.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="primary">Gemini 3.8 Flash</Badge>
          <Badge variant="outline">Gemini Embedding 2</Badge>
          <Badge variant="warning">Stage 1: Placeholder</Badge>
        </div>
      </div>

      {/* Agent Overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card className="md:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                <BookOpenCheck className="h-5 w-5" />
              </div>
              <Badge variant="primary">Agent 4: Policy Brain</Badge>
            </div>
            <CardTitle className="text-base">Bilingual Policy Query Assistant</CardTitle>
            <CardDescription className="text-xs">
              Retrieval-Augmented Generation using PostgreSQL 16 pgvector embeddings.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-4">
            <p>
              Employees can inquire in English or Urdu regarding leave balances, maternity/paternity
              policies, remote work regulations, and reimbursement rules. The system performs vector
              similarity search across approved company policy documents and cites exact source
              clauses.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
                <span className="font-semibold text-textPrimary flex items-center gap-1.5">
                  <Languages className="h-3.5 w-3.5 text-primary" />
                  Urdu Native Support
                </span>
                <span>
                  Trained and evaluated on bilingual policy corpuses with Matryoshka dimension
                  embeddings.
                </span>
              </div>
              <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
                <span className="font-semibold text-textPrimary flex items-center gap-1.5">
                  <ShieldCheck className="h-3.5 w-3.5 text-primary" />
                  Source-Citations Only
                </span>
                <span>
                  Zero hallucinations: answers must strictly cite approved handbook paragraphs.
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-bold text-textPrimary">
              Vector Store (ADR 002)
            </CardTitle>
            <CardDescription className="text-xs">pgvector Configuration</CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Database Extension</span>
              <span className="font-semibold text-textPrimary">
                pgvector (Active in Postgres 16)
              </span>
            </div>
            <div className="border-b border-appBorder pb-2">
              <span className="text-textMuted block">Embedding Dimensions</span>
              <span className="font-semibold text-textPrimary">768 / 1536 (MRL Optimized)</span>
            </div>
            <div>
              <span className="text-textMuted block">Multi-Tenant Isolation</span>
              <span className="font-semibold text-textPrimary">
                Row-Level Security on vector queries
              </span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Mock Chat / Search Interface */}
      <Card className="border-appBorder bg-surface">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              <CardTitle className="text-sm font-semibold text-textPrimary">
                Interactive Policy Assistant (Sample Inquiries)
              </CardTitle>
            </div>
            <Badge variant="outline">Stage 3 Preview</Badge>
          </div>
          <CardDescription className="text-xs">
            Synthesized policy documents from Acme Tech Solutions (6 policies in English & Urdu).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="p-3 rounded-medium bg-appBg border border-appBorder text-xs space-y-1">
              <div className="flex items-center justify-between text-textMuted text-[10px]">
                <span>Employee Query (Urdu):</span>
                <span>Urdu Language Detected</span>
              </div>
              <p className="font-semibold text-textPrimary text-right font-sans" dir="rtl">
                کیا مجھے سالانہ چھٹیوں کے دوران بیماری کی رخصت مل سکتی ہے؟
              </p>
            </div>

            <div className="p-3 rounded-medium bg-primary/5 border border-primary/20 text-xs space-y-1">
              <div className="flex items-center justify-between text-primary text-[10px] font-semibold">
                <span>Policy Brain Response:</span>
                <span>Cited: Acme Leave Policy Sec 4.2</span>
              </div>
              <p className="text-textPrimary text-right font-sans" dir="rtl">
                جی ہاں، اگر سالانہ رخصت کے دوران آپ بیمار ہو جائیں تو مستند ڈاکٹر کی تصدیق شدہ
                میڈیکل رپورٹ جمع کرا کر ان دنوں کو بیماری کی رخصت (Sick Leave) میں تبدیل کیا جا سکتا
                ہے۔
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
