"use client";

import React, { useId, useRef, useState } from "react";
import { CheckCircle2, Download, FileSpreadsheet, UploadCloud, XCircle } from "lucide-react";
import type {
  AttendanceImportCommitResponse,
  AttendanceImportValidationResult,
} from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useCommitAttendanceImport, useValidateAttendanceImport } from "@/hooks/use-attendance";
import { triggerBrowserBlobDownload } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

interface AttendanceImportTabProps {
  canManage: boolean;
  isOffline: boolean;
}

const FORMULA_PREFIXES = ["=", "+", "-", "@", "\t", "\r"];

function escapeCsvCell(raw: string | number | null | undefined): string {
  let s = String(raw ?? "");
  if (FORMULA_PREFIXES.some((p) => s.trimStart().startsWith(p))) {
    s = `'${s}`;
  }
  return `"${s.replaceAll('"', '""')}"`;
}

export function AttendanceImportTab({ canManage, isOffline }: AttendanceImportTabProps) {
  const fileInputId = useId();
  const tableCaptionId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const validateMutation = useValidateAttendanceImport();
  const commitMutation = useCommitAttendanceImport();
  const pushToast = useToastStore((s) => s.pushToast);

  const [validationResult, setValidationResult] = useState<AttendanceImportValidationResult | null>(
    null
  );
  const [commitResult, setCommitResult] = useState<AttendanceImportCommitResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);

  const canCommitBatch =
    Boolean(validationResult) &&
    Boolean(validationResult?.can_commit) &&
    (validationResult?.valid_rows ?? 0) > 0 &&
    (validationResult?.invalid_rows ?? 0) === 0 &&
    commitResult === null &&
    !commitMutation.isPending &&
    canManage &&
    !isOffline;

  const handleUpload = async (file: File | undefined | null) => {
    if (!file || !canManage || isOffline) return;
    setValidationResult(null);
    setCommitResult(null);
    setErrorMessage(null);

    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setErrorMessage("Only .xlsx Excel workbooks are supported for attendance import.");
      return;
    }

    try {
      const result = await validateMutation.mutateAsync(file);
      setValidationResult(result);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to validate attendance Excel file.";
      setErrorMessage(msg);
      pushToast({ variant: "error", title: "Validation Failed", description: msg });
    } finally {
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const handleDownloadCsvErrors = () => {
    if (!validationResult) return;
    const header = ["row_number", "employee_email", "attendance_date", "status", "notes", "errors"];
    const badRows = validationResult.preview_rows.filter((r) => !r.is_valid);
    const lines = [
      header.map((c) => escapeCsvCell(c)).join(","),
      ...badRows.map((r) =>
        [
          escapeCsvCell(r.row_number),
          escapeCsvCell(r.employee_email),
          escapeCsvCell(r.attendance_date),
          escapeCsvCell(r.status),
          escapeCsvCell(r.notes ?? ""),
          escapeCsvCell((r.errors ?? []).join("; ")),
        ].join(",")
      ),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
    triggerBrowserBlobDownload(blob, `attendance_errors_${validationResult.import_batch_id}.csv`);
  };

  const handleCommit = async () => {
    if (!validationResult || !canCommitBatch) return;
    setErrorMessage(null);
    try {
      const res = await commitMutation.mutateAsync(validationResult.import_batch_id);
      setCommitResult(res);
      pushToast({
        variant: "success",
        title: "Attendance Imported",
        description: `Committed ${res.committed_count} row(s) across ${res.year_months.join(", ")} and queued ${res.sheets_queued} Sheets update(s).`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to commit attendance import.";
      setErrorMessage(msg);
      pushToast({ variant: "error", title: "Commit Failed", description: msg });
    }
  };

  return (
    <div className="space-y-4">
      {errorMessage && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900"
        >
          <XCircle className="h-4 w-4 text-rose-600 shrink-0" aria-hidden="true" />
          <span className="font-medium">{errorMessage}</span>
        </div>
      )}

      {commitResult && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 rounded-medium border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-semibold text-emerald-900"
        >
          <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" aria-hidden="true" />
          <span>
            Committed {commitResult.committed_count} attendance record(s) with{" "}
            {commitResult.sheets_queued} Sheets sync row(s) queued.
          </span>
        </div>
      )}

      <Card className="border border-appBorder bg-white shadow-sm">
        <CardHeader>
          <div className="flex items-center gap-2">
            <FileSpreadsheet className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
            <CardTitle className="text-base text-[#102C54]">
              Two-Phase Excel Attendance Import (.xlsx)
            </CardTitle>
          </div>
          <CardDescription className="text-xs text-textSecondary">
            Upload `.xlsx` workbooks with columns `employee_email`, `date` (`YYYY-MM-DD`), `status`,
            and optional `notes`. Clean batches commit atomically
            (`source=&quot;excel_import&quot;`).
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-5">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragActive(true);
            }}
            onDragLeave={() => setIsDragActive(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragActive(false);
              void handleUpload(e.dataTransfer.files?.[0]);
            }}
            className={`rounded-medium border-2 border-dashed p-6 text-center transition-colors ${
              isDragActive
                ? "border-[#2A5BDB] bg-[#2A5BDB]/5"
                : "border-slate-300 bg-[#F8F9FD] hover:border-[#2A5BDB]/60"
            }`}
          >
            <UploadCloud className="mx-auto mb-2 h-8 w-8 text-[#2A5BDB]" aria-hidden="true" />
            <p className="text-sm font-semibold text-[#102C54]">
              Drag &amp; Drop Attendance Workbook (.xlsx)
            </p>
            <p className="mt-1 text-xs text-textSecondary">
              Validates magic bytes, employee directory membership, valid statuses, and unlocked
              months before commit.
            </p>
            <div className="mt-4">
              <label
                htmlFor={fileInputId}
                className={`inline-flex items-center gap-2 rounded-small px-4 py-2 text-xs font-semibold text-white transition-colors ${
                  canManage && !validateMutation.isPending && !isOffline
                    ? "cursor-pointer bg-[#2A5BDB] hover:bg-[#102C54]"
                    : "cursor-not-allowed bg-slate-400"
                }`}
              >
                <UploadCloud className="h-3.5 w-3.5" aria-hidden="true" />
                {validateMutation.isPending ? "Validating Workbook..." : "Select Attendance .xlsx"}
              </label>
              <input
                ref={fileInputRef}
                id={fileInputId}
                type="file"
                accept=".xlsx"
                aria-label="Upload attendance Excel workbook"
                disabled={!canManage || validateMutation.isPending || isOffline}
                onChange={(e) => void handleUpload(e.target.files?.[0])}
                className="sr-only"
              />
            </div>
          </div>

          {validationResult && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 text-xs">
                <div className="rounded-small border border-appBorder bg-[#F8F9FD] p-3">
                  <span className="text-[11px] font-semibold uppercase text-textSecondary">
                    Total Rows
                  </span>
                  <p className="mt-1 text-2xl font-bold text-[#102C54]">
                    {validationResult.total_rows}
                  </p>
                </div>
                <div className="rounded-small border border-emerald-200 bg-emerald-50 p-3">
                  <span className="text-[11px] font-semibold uppercase text-emerald-800">
                    Valid Rows
                  </span>
                  <p className="mt-1 text-2xl font-bold text-emerald-700">
                    {validationResult.valid_rows}
                  </p>
                </div>
                <div className="rounded-small border border-rose-200 bg-rose-50 p-3">
                  <span className="text-[11px] font-semibold uppercase text-rose-800">
                    Invalid Rows
                  </span>
                  <p className="mt-1 text-2xl font-bold text-rose-700">
                    {validationResult.invalid_rows}
                  </p>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2">
                {validationResult.invalid_rows > 0 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={handleDownloadCsvErrors}
                    className="border-rose-200 text-rose-800 hover:bg-rose-50 text-xs inline-flex items-center gap-1.5"
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    Download Error Report (.csv)
                  </Button>
                ) : (
                  <span className="text-xs font-semibold text-emerald-700">
                    All rows validated cleanly — ready for atomic commit.
                  </span>
                )}

                <Button
                  type="button"
                  size="sm"
                  disabled={!canCommitBatch}
                  onClick={() => void handleCommit()}
                  className="bg-[#102C54] text-white hover:bg-[#2A5BDB] text-xs inline-flex items-center gap-1.5"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                  {commitMutation.isPending ? "Committing..." : "Commit Valid Records"}
                </Button>
              </div>

              <div
                tabIndex={0}
                role="region"
                aria-labelledby={tableCaptionId}
                className="overflow-auto rounded-medium border border-appBorder max-h-[360px]"
              >
                <table className="w-full min-w-[680px] border-separate border-spacing-0 text-left text-xs">
                  <caption id={tableCaptionId} className="sr-only">
                    Validated attendance Excel preview rows
                  </caption>
                  <thead className="bg-[#F8F9FD] text-[11px] uppercase text-textSecondary sticky top-0">
                    <tr>
                      <th scope="col" className="border-b border-appBorder px-3 py-2">
                        Row
                      </th>
                      <th scope="col" className="border-b border-appBorder px-3 py-2">
                        Employee Email
                      </th>
                      <th scope="col" className="border-b border-appBorder px-3 py-2">
                        Date
                      </th>
                      <th scope="col" className="border-b border-appBorder px-3 py-2">
                        Status
                      </th>
                      <th scope="col" className="border-b border-appBorder px-3 py-2">
                        Validation Result
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-appBorder bg-white">
                    {validationResult.preview_rows.map((row) => (
                      <tr
                        key={row.row_number}
                        className={row.is_valid ? "bg-white" : "bg-rose-50/60"}
                      >
                        <td className="px-3 py-2 font-mono">{row.row_number}</td>
                        <td className="px-3 py-2 font-medium text-[#102C54]">
                          {row.employee_email || "—"}
                        </td>
                        <td className="px-3 py-2 font-mono">{row.attendance_date || "—"}</td>
                        <td className="px-3 py-2 font-mono">{row.status || "—"}</td>
                        <td className="px-3 py-2">
                          {row.is_valid ? (
                            <Badge variant="success" className="text-[10px]">
                              Valid
                            </Badge>
                          ) : (
                            <span className="text-[11px] font-medium text-rose-800">
                              {(row.errors ?? []).join("; ")}
                            </span>
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
    </div>
  );
}
