"use client";

import React, { useId, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  UploadCloud,
  XCircle,
} from "lucide-react";
import type { ImportCommitResponse, ImportValidationResult } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useCommitEmployeeImport,
  useDownloadImportErrorReport,
  useDownloadImportTemplate,
  useValidateEmployeeImport,
} from "@/hooks/use-employee-import";
import { useCurrentUser } from "@/hooks/use-current-user";

interface Step4EmployeeImportProps {
  isSavingStep: boolean;
  onBack: () => void;
  onSaveAndContinue: () => void;
}

export function Step4EmployeeImport({
  isSavingStep,
  onBack,
  onSaveAndContinue,
}: Step4EmployeeImportProps) {
  const fileInputId = useId();
  const tableCaptionId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const { data: currentUser } = useCurrentUser();
  const downloadTemplate = useDownloadImportTemplate();
  const validateImport = useValidateEmployeeImport();
  const downloadErrorReport = useDownloadImportErrorReport();
  const commitImport = useCommitEmployeeImport();

  const [validationResult, setValidationResult] = useState<ImportValidationResult | null>(null);
  const [commitResult, setCommitResult] = useState<ImportCommitResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);

  const userRole = currentUser?.role?.toLowerCase() || "owner";
  const canManage =
    userRole === "owner" || userRole === "manager" || currentUser?.is_hr_head === true;

  const canCommitBatch =
    Boolean(validationResult) &&
    (validationResult?.valid_count ?? 0) > 0 &&
    (validationResult?.invalid_count ?? 0) === 0 &&
    commitResult === null &&
    !commitImport.isPending &&
    canManage;

  const handleDownloadTemplate = async () => {
    setErrorMessage(null);
    try {
      await downloadTemplate.mutateAsync();
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to download Excel template.");
    }
  };

  const handleUploadSpreadsheet = async (file: File | undefined | null) => {
    if (!file || !canManage) return;

    // Reset prior batch validation & commit results immediately when a new file is chosen
    setValidationResult(null);
    setCommitResult(null);
    setErrorMessage(null);

    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setErrorMessage("Only .xlsx Excel workbooks are supported for bulk employee import.");
      return;
    }

    try {
      const result = await validateImport.mutateAsync(file);
      setValidationResult(result);
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to validate spreadsheet.");
    } finally {
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const handleDownloadErrors = async () => {
    if (!validationResult) return;
    setErrorMessage(null);
    try {
      await downloadErrorReport.mutateAsync(validationResult.import_batch_id);
    } catch (err: unknown) {
      setErrorMessage(
        err instanceof Error ? err.message : "Failed to download Excel error report."
      );
    }
  };

  const handleCommitBatch = async () => {
    if (!validationResult || !canCommitBatch) return;
    setErrorMessage(null);
    try {
      const committed = await commitImport.mutateAsync(validationResult.import_batch_id);
      setCommitResult(committed);
    } catch (err: unknown) {
      setErrorMessage(
        err instanceof Error ? err.message : "Failed to commit employee import batch."
      );
    }
  };

  return (
    <div className="space-y-6">
      {errorMessage && (
        <div
          role="alert"
          className="flex items-center justify-between rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
            <span className="font-medium">{errorMessage}</span>
          </div>
          <button
            type="button"
            onClick={() => setErrorMessage(null)}
            className="font-bold text-rose-700 hover:text-rose-900 px-1"
            aria-label="Dismiss error"
          >
            &times;
          </button>
        </div>
      )}

      {commitResult && (
        <div
          role="status"
          aria-live="polite"
          className="flex flex-wrap items-center justify-between gap-2 rounded-medium border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-semibold text-emerald-900"
        >
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
            <span>
              Committed {commitResult.committed_count} employees to PostgreSQL with encrypted PII
              and queued {commitResult.sheets_queued} Google Sheets sync updates.
            </span>
          </div>
        </div>
      )}

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-medium bg-[#2A5BDB]/10">
                <FileSpreadsheet className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              </div>
              <div>
                <CardTitle className="text-base text-[#102C54]">
                  Step 4 — Bulk Add Employees via Excel (.xlsx)
                </CardTitle>
                <CardDescription className="text-xs text-textSecondary">
                  Download your department-aware Excel template, upload filled rows for
                  deterministic validation, and commit with AES-256-GCM PII encryption.
                </CardDescription>
              </div>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleDownloadTemplate()}
              disabled={downloadTemplate.isPending || !canManage}
              className="border-[#2A5BDB]/30 text-[#2A5BDB] hover:bg-[#F8F9FD] inline-flex items-center gap-1.5"
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              {downloadTemplate.isPending ? "Generating..." : "Download Excel Template (.xlsx)"}
            </Button>
          </div>
        </CardHeader>

        <CardContent className="space-y-5">
          {/* Upload Dropzone */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragActive(true);
            }}
            onDragLeave={() => setIsDragActive(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragActive(false);
              void handleUploadSpreadsheet(e.dataTransfer.files?.[0]);
            }}
            className={`rounded-medium border-2 border-dashed p-6 text-center transition-colors ${
              isDragActive
                ? "border-[#2A5BDB] bg-[#2A5BDB]/5"
                : "border-slate-300 bg-[#F8F9FD] hover:border-[#2A5BDB]/60"
            }`}
          >
            <UploadCloud className="mx-auto mb-2 h-8 w-8 text-[#2A5BDB]" aria-hidden="true" />
            <p className="text-sm font-semibold text-[#102C54]">
              Upload Filled Employee Workbook (.xlsx)
            </p>
            <p className="mt-1 text-xs text-textSecondary">
              Every row is validated before committing. Max 500 rows / 5MB per batch.
            </p>
            <div className="mt-4">
              <label
                htmlFor={fileInputId}
                className={`inline-flex items-center gap-2 rounded-small px-4 py-2 text-xs font-semibold text-white transition-colors ${
                  canManage && !validateImport.isPending
                    ? "cursor-pointer bg-[#2A5BDB] hover:bg-[#102C54]"
                    : "cursor-not-allowed bg-slate-400"
                }`}
              >
                <UploadCloud className="h-3.5 w-3.5" aria-hidden="true" />
                {validateImport.isPending ? "Validating Rows..." : "Upload Filled .xlsx"}
              </label>
              <input
                ref={fileInputRef}
                id={fileInputId}
                type="file"
                accept=".xlsx"
                aria-label="Upload filled employee Excel workbook"
                disabled={!canManage || validateImport.isPending}
                onChange={(e) => void handleUploadSpreadsheet(e.target.files?.[0])}
                className="sr-only"
              />
            </div>
          </div>

          {/* Validation Summary Cards + Preview Table */}
          {validationResult && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div className="rounded-small border border-appBorder bg-[#F8F9FD] p-3.5">
                  <span className="block text-[11px] font-semibold uppercase tracking-wider text-textSecondary">
                    Total Rows
                  </span>
                  <span className="mt-1 block text-2xl font-bold text-[#102C54]">
                    {validationResult.total_rows}
                  </span>
                </div>
                <div className="rounded-small border border-emerald-200 bg-emerald-50/70 p-3.5">
                  <span className="block text-[11px] font-semibold uppercase tracking-wider text-emerald-700">
                    Valid
                  </span>
                  <span className="mt-1 block text-2xl font-bold text-emerald-800">
                    {validationResult.valid_count}
                  </span>
                </div>
                <div
                  className={`rounded-small border p-3.5 ${
                    validationResult.invalid_count > 0
                      ? "border-rose-200 bg-rose-50"
                      : "border-appBorder bg-[#F8F9FD]"
                  }`}
                >
                  <span
                    className={`block text-[11px] font-semibold uppercase tracking-wider ${
                      validationResult.invalid_count > 0 ? "text-rose-700" : "text-textSecondary"
                    }`}
                  >
                    Errors
                  </span>
                  <span
                    className={`mt-1 block text-2xl font-bold ${
                      validationResult.invalid_count > 0 ? "text-rose-800" : "text-[#102C54]"
                    }`}
                  >
                    {validationResult.invalid_count}
                  </span>
                </div>
              </div>

              {/* Action Bar for Error Report vs Clean Commit */}
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-small border border-appBorder bg-[#F8F9FD] p-3">
                <div className="text-xs text-textSecondary">
                  Batch:{" "}
                  <span className="font-mono font-semibold text-textPrimary">
                    {validationResult.file_name}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {validationResult.invalid_count > 0 && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => void handleDownloadErrors()}
                      disabled={downloadErrorReport.isPending}
                      className="border-rose-300 bg-rose-50 text-rose-800 hover:bg-rose-100 inline-flex items-center gap-1.5"
                    >
                      <Download className="h-3.5 w-3.5" aria-hidden="true" />
                      {downloadErrorReport.isPending
                        ? "Preparing Report..."
                        : "Download Error Report (.xlsx)"}
                    </Button>
                  )}

                  <Button
                    type="button"
                    size="sm"
                    onClick={() => void handleCommitBatch()}
                    disabled={!canCommitBatch}
                    className="bg-[#2A5BDB] text-white hover:bg-[#102C54] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {commitImport.isPending
                      ? "Committing Employees..."
                      : commitResult
                        ? "Employees Committed"
                        : "Confirm & Commit Employees"}
                  </Button>
                </div>
              </div>

              {/* Interactive Preview Table */}
              <div
                tabIndex={0}
                role="region"
                aria-labelledby={tableCaptionId}
                className="max-h-96 overflow-auto rounded-small border border-appBorder"
              >
                <table className="w-full border-collapse text-left text-xs">
                  <caption id={tableCaptionId} className="sr-only">
                    Validated employee Excel import preview with masked sensitive identifiers
                  </caption>
                  <thead className="sticky top-0 bg-[#102C54] text-white">
                    <tr>
                      <th scope="col" className="px-3 py-2 font-semibold">
                        Row
                      </th>
                      <th scope="col" className="px-3 py-2 font-semibold">
                        Employee
                      </th>
                      <th scope="col" className="px-3 py-2 font-semibold">
                        Department
                      </th>
                      <th scope="col" className="px-3 py-2 font-semibold">
                        Join Date
                      </th>
                      <th scope="col" className="px-3 py-2 font-semibold">
                        CNIC (Masked)
                      </th>
                      <th scope="col" className="px-3 py-2 font-semibold">
                        Bank (Masked)
                      </th>
                      <th scope="col" className="px-3 py-2 font-semibold">
                        Validation
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-appBorder bg-white">
                    {validationResult.preview_rows.map((row) => (
                      <tr
                        key={row.row_number}
                        className={row.is_valid ? "hover:bg-[#F8F9FD]" : "bg-rose-50/40"}
                      >
                        <th
                          scope="row"
                          className="px-3 py-2 font-mono font-semibold text-[#102C54]"
                        >
                          {row.row_number}
                        </th>
                        <td className="px-3 py-2">
                          <span className="block font-semibold text-textPrimary">
                            {row.full_name || "—"}
                          </span>
                          <span className="block text-[11px] text-textSecondary">{row.email}</span>
                        </td>
                        <td className="px-3 py-2 text-textSecondary">
                          {row.department} • {row.job_title}
                        </td>
                        <td className="px-3 py-2 font-mono text-[11px] text-textSecondary">
                          {row.join_date}
                        </td>
                        <td className="px-3 py-2 font-mono text-[11px] text-textSecondary">
                          {row.cnic_masked || "—"}
                        </td>
                        <td className="px-3 py-2 font-mono text-[11px] text-textSecondary">
                          {row.bank_account_masked || "—"}
                        </td>
                        <td className="px-3 py-2">
                          {row.is_valid ? (
                            <Badge variant="success">Valid</Badge>
                          ) : (
                            <div className="space-y-1">
                              <Badge
                                variant="outline"
                                className="border-rose-300 bg-rose-50 text-rose-700"
                              >
                                Error
                              </Badge>
                              <p className="text-[11px] font-medium text-rose-700">
                                {(row.errors ?? []).join("; ")}
                              </p>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center justify-between pt-2">
        <Button type="button" variant="outline" onClick={onBack}>
          <ArrowLeft className="mr-1.5 h-4 w-4" aria-hidden="true" />
          Back
        </Button>
        <Button
          type="button"
          onClick={onSaveAndContinue}
          disabled={isSavingStep || !canManage}
          className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-2"
        >
          {isSavingStep ? "Saving Step 4..." : "Save & Continue to Test & Launch"}
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
