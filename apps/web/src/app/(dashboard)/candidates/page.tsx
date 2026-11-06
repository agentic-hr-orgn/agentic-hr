"use client";

import React from "react";
import Link from "next/link";
import {
  FileCheck2,
  Users2,
  UploadCloud,
  FileSpreadsheet,
  CheckCircle2,
  ArrowLeft,
  ShieldAlert,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export default function CandidatesPage() {
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
            <span className="text-xs text-textPrimary font-semibold">Candidates</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-textPrimary">
            Candidate Pipeline & Screening
          </h1>
          <p className="text-xs md:text-sm text-textSecondary">
            DocuVerify multi-page OCR extraction and Screener 0-100 rubric-based candidate
            evaluations.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="primary">Gemini 3.5 Flash-Lite</Badge>
          <Badge variant="warning">Stage 1: Placeholder</Badge>
        </div>
      </div>

      {/* Agents Active in this Vertical Slice */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                <FileCheck2 className="h-5 w-5" />
              </div>
              <Badge variant="primary">Agent 1: DocuVerify</Badge>
            </div>
            <CardTitle className="text-base">Document Ingestion & Validation</CardTitle>
            <CardDescription className="text-xs">
              Docling OCR parser for native PDFs and scanned CVs.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <p>
              Extracts structured candidate profiles from CVs, diplomas, and identity documents.
              Handles low-quality mobile scans, table extractions, and multilingual Urdu/English
              resumes.
            </p>
            <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
              <span className="font-semibold text-textPrimary block">Stage 2 Milestone:</span>
              <span>
                Upload pipeline with Google Drive least-privilege connector (drive.file scope).
              </span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between mb-2">
              <div className="h-9 w-9 rounded-medium bg-primary/10 flex items-center justify-center text-primary">
                <Users2 className="h-5 w-5" />
              </div>
              <Badge variant="primary">Agent 2: Screener</Badge>
            </div>
            <CardTitle className="text-base">0-100 Rubric Scoring & Ranking</CardTitle>
            <CardDescription className="text-xs">
              Auditable rubric scoring against custom job requirements.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs text-textSecondary space-y-3">
            <p>
              Scores candidate qualifications against defined weighted criteria. Outputs detailed
              reasoning logs, strengths, concerns, and recommended interview topics.
            </p>
            <div className="p-3 bg-appBg rounded-small border border-appBorder space-y-1">
              <span className="font-semibold text-textPrimary block">
                LLM Proposes, Code Decides:
              </span>
              <span>
                Screening decisions require recruiter sign-off before candidate progression.
              </span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Mock Upload / Candidate Pipeline Area */}
      <Card className="border-dashed border-2 border-appBorder p-8 text-center bg-surface">
        <div className="max-w-md mx-auto space-y-4">
          <div className="h-12 w-12 rounded-full bg-primary/10 text-primary flex items-center justify-center mx-auto">
            <UploadCloud className="h-6 w-6" />
          </div>
          <div>
            <h3 className="text-base font-bold text-textPrimary">CV Ingestion Sandbox</h3>
            <p className="text-xs text-textSecondary mt-1">
              The candidate upload and automated screening pipeline will be connected in Stage 2.
              Synthetic CVs from Acme Tech Solutions (10 test CVs) are ready for end-to-end
              evaluation.
            </p>
          </div>
          <div className="flex justify-center gap-3">
            <Button variant="outline" size="sm" disabled>
              <FileSpreadsheet className="h-3.5 w-3.5 mr-1.5" />
              Sync to Google Sheets
            </Button>
            <Button variant="primary" size="sm" disabled>
              <UploadCloud className="h-3.5 w-3.5 mr-1.5" />
              Upload Scanned CV
            </Button>
          </div>
        </div>
      </Card>

      {/* Privacy & Synthetic Data Notice */}
      <div className="flex items-start gap-3 p-4 rounded-medium bg-amber-500/10 border border-amber-500/30 text-xs text-amber-900">
        <ShieldAlert className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
        <div>
          <span className="font-semibold block">Privacy & Security Boundary (ADR 003):</span>
          <span>
            Only synthetic candidate CVs from Acme Tech Solutions are routed through free-tier
            Google AI Studio endpoints. Candidate CVs are strictly treated as untrusted data with
            multi-layer prompt-injection defense.
          </span>
        </div>
      </div>
    </div>
  );
}
