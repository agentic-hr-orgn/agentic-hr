"use client";

import React, { useId, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CalendarPlus,
  CheckCircle2,
  ClipboardList,
  Send,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import type { LeaveIntakeResponse, LeaveTypeResponse } from "@agentic-hr/contracts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PolicyCheckBadge, formatDays } from "@/components/leave/leave-status-badges";
import { useLeaveIntake, useSubmitLeaveRequest } from "@/hooks/use-leave";
import { useToastStore } from "@/stores/toast-store";

interface LeaveRequestTabProps {
  leaveTypes: LeaveTypeResponse[];
  isOffline: boolean;
  onSubmitted?: () => void;
}

type RequestMode = "assistant" | "form";

export function LeaveRequestTab({ leaveTypes, isOffline, onSubmitted }: LeaveRequestTabProps) {
  const aiInputId = useId();
  const typeSelectId = useId();
  const startDateId = useId();
  const endDateId = useId();
  const halfDayId = useId();
  const halfDayPeriodId = useId();
  const reasonId = useId();
  const documentIdInput = useId();

  const [mode, setMode] = useState<RequestMode>("assistant");
  const [actionError, setActionError] = useState<string | null>(null);

  // AI Leave Intake state
  const [chatInput, setChatInput] = useState("");
  const [conversationContext, setConversationContext] = useState<Record<string, unknown> | null>(
    null
  );
  const [intakeResult, setIntakeResult] = useState<LeaveIntakeResponse | null>(null);

  // Plain Form state
  const [leaveTypeCode, setLeaveTypeCode] = useState<string>(leaveTypes[0]?.code || "annual");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [isHalfDay, setIsHalfDay] = useState(false);
  const [halfDayPeriod, setHalfDayPeriod] = useState<"morning" | "afternoon">("morning");
  const [reason, setReason] = useState("");
  const [documentId, setDocumentId] = useState("");

  const intakeMutation = useLeaveIntake();
  const submitMutation = useSubmitLeaveRequest();
  const pushToast = useToastStore((s) => s.pushToast);

  const activeLeaveTypes = leaveTypes.filter((lt) => lt.is_active);
  const selectedLeaveTypeCode =
    activeLeaveTypes.find((lt) => lt.code === leaveTypeCode)?.code ??
    activeLeaveTypes[0]?.code ??
    leaveTypeCode;

  const handleRunIntake = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim() || intakeMutation.isPending || isOffline) return;
    setActionError(null);
    try {
      const res = await intakeMutation.mutateAsync({
        message: chatInput.trim(),
        conversation_context: conversationContext,
      });
      setIntakeResult(res);
      setConversationContext(res.extracted ?? {});
      setChatInput("");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to parse leave request.";
      setActionError(msg);
      pushToast({ variant: "error", title: "AI Leave Assistant Error", description: msg });
    }
  };

  const handleConfirmCardSubmit = async () => {
    const card = intakeResult?.confirm_card;
    if (!card || card.policy_check_status === "BLOCKED" || submitMutation.isPending || isOffline) {
      return;
    }
    setActionError(null);
    try {
      const created = await submitMutation.mutateAsync({
        leave_type_id: card.leave_type_id,
        leave_type_code: card.leave_type_code,
        start_date: card.start_date,
        end_date: card.end_date,
        is_half_day: card.is_half_day,
        half_day_period: card.is_half_day ? (card.half_day_period ?? "morning") : undefined,
        reason: card.reason,
        document_id: card.document_id ?? undefined,
        start_workflow: true,
      });
      setIntakeResult(null);
      setConversationContext(null);
      pushToast({
        variant: "success",
        title: "Leave Request Submitted",
        description: `Submitted ${created.leave_type_name} (${created.start_date} to ${created.end_date}, status: ${created.status}).`,
      });
      onSubmitted?.();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to submit leave request.";
      setActionError(msg);
      pushToast({ variant: "error", title: "Submission Failed", description: msg });
    }
  };

  const handlePlainFormSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitMutation.isPending || isOffline) return;
    if (!startDate || !endDate || reason.trim().length < 2) {
      setActionError("Start date, end date, and a brief reason (2+ chars) are required.");
      return;
    }
    setActionError(null);
    try {
      const created = await submitMutation.mutateAsync({
        leave_type_code: selectedLeaveTypeCode,
        start_date: startDate,
        end_date: isHalfDay ? startDate : endDate,
        is_half_day: isHalfDay,
        half_day_period: isHalfDay ? halfDayPeriod : undefined,
        reason: reason.trim(),
        document_id: documentId.trim() ? documentId.trim() : undefined,
        start_workflow: true,
      });
      setReason("");
      setDocumentId("");
      setIsHalfDay(false);
      pushToast({
        variant: "success",
        title: "Leave Request Submitted",
        description: `Submitted ${created.leave_type_name} (${formatDays(created.working_days)} working day(s), status: ${created.status}).`,
      });
      onSubmitted?.();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to submit leave request.";
      setActionError(msg);
      pushToast({ variant: "error", title: "Submission Failed", description: msg });
    }
  };

  const confirmCard = intakeResult?.confirm_card ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-medium border border-appBorder bg-white p-1">
          <button
            type="button"
            onClick={() => setMode("assistant")}
            className={`inline-flex items-center gap-1.5 rounded-small px-3 py-1.5 text-xs font-semibold transition-colors ${
              mode === "assistant"
                ? "bg-[#2A5BDB] text-white"
                : "text-textSecondary hover:text-[#102C54]"
            }`}
          >
            <Bot className="h-3.5 w-3.5" aria-hidden="true" />
            AI Leave Assistant (English / اردو)
          </button>
          <button
            type="button"
            onClick={() => setMode("form")}
            className={`inline-flex items-center gap-1.5 rounded-small px-3 py-1.5 text-xs font-semibold transition-colors ${
              mode === "form"
                ? "bg-[#2A5BDB] text-white"
                : "text-textSecondary hover:text-[#102C54]"
            }`}
          >
            <ClipboardList className="h-3.5 w-3.5" aria-hidden="true" />
            Plain Leave Form
          </button>
        </div>
        <span className="text-[11px] text-textSecondary">
          Deterministic policy rules validate working days, balances, notice &amp; team coverage.
        </span>
      </div>

      {actionError && (
        <div
          role="alert"
          className="rounded-medium border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900 font-medium"
        >
          {actionError}
        </div>
      )}

      {mode === "assistant" ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card className="border border-appBorder bg-white shadow-sm">
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
                  <CardTitle className="text-base text-[#102C54]">
                    Conversational Leave Intake
                  </CardTitle>
                </div>
                <Badge variant="primary">Bilingual (EN / UR)</Badge>
              </div>
              <CardDescription className="text-xs text-textSecondary">
                Describe your leave in English, Urdu script, or Roman Urdu (e.g. &quot;Mujhe kal se
                2 din ki annual chutti chahiye&quot;).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {intakeResult?.status === "needs_clarification" &&
                intakeResult.clarifying_question && (
                  <div
                    role="status"
                    aria-live="polite"
                    className="rounded-medium border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950 space-y-1"
                  >
                    <p className="font-bold text-[#102C54]">Assistant Clarifying Question:</p>
                    <p dir="auto">{intakeResult.clarifying_question}</p>
                  </div>
                )}

              <form onSubmit={handleRunIntake} className="space-y-3">
                <div>
                  <label
                    htmlFor={aiInputId}
                    className="block text-xs font-semibold text-[#102C54] mb-1.5"
                  >
                    Leave Request Message
                  </label>
                  <textarea
                    id={aiInputId}
                    dir="auto"
                    rows={3}
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    disabled={intakeMutation.isPending || isOffline}
                    placeholder="e.g. Need annual leave from 2026-12-07 to 2026-12-08 for family wedding"
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54] focus:border-[#2A5BDB] focus:outline-none focus:ring-2 focus:ring-[#2A5BDB]/20"
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="submit"
                    disabled={intakeMutation.isPending || !chatInput.trim() || isOffline}
                    className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1.5 text-xs"
                  >
                    <Send className="h-3.5 w-3.5" aria-hidden="true" />
                    {intakeMutation.isPending ? "Analyzing..." : "Analyze Leave Request"}
                  </Button>
                  {(intakeResult || conversationContext) && (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        setIntakeResult(null);
                        setConversationContext(null);
                        setChatInput("");
                      }}
                      className="text-xs"
                    >
                      Start Over
                    </Button>
                  )}
                </div>
              </form>
            </CardContent>
          </Card>

          <Card className="border border-appBorder bg-white shadow-sm">
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-base text-[#102C54]">
                  Pre-Submission Confirm Card
                </CardTitle>
                {confirmCard && <PolicyCheckBadge status={confirmCard.policy_check_status} />}
              </div>
              <CardDescription className="text-xs text-textSecondary">
                Code-verified working days, balance impact, and deterministic policy rules.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {!confirmCard ? (
                <div className="rounded-medium border border-dashed border-appBorder bg-[#F8F9FD] px-4 py-8 text-center text-xs text-textSecondary">
                  Submit a message to generate a verified Confirm Card before applying.
                </div>
              ) : (
                <div className="space-y-4 text-xs">
                  <dl className="grid grid-cols-2 gap-2.5 rounded-small border border-appBorder bg-[#F8F9FD] p-3">
                    <div>
                      <dt className="text-[11px] text-textSecondary">Leave Type</dt>
                      <dd className="font-bold text-[#102C54]">{confirmCard.leave_type_name}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] text-textSecondary">Working Days</dt>
                      <dd className="font-bold text-[#2A5BDB]">
                        {formatDays(confirmCard.working_days)} day(s)
                        {confirmCard.is_half_day
                          ? ` (${confirmCard.half_day_period || "half"})`
                          : ""}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] text-textSecondary">Dates</dt>
                      <dd className="font-semibold text-[#102C54]">
                        {confirmCard.start_date} to {confirmCard.end_date}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] text-textSecondary">Balance Impact</dt>
                      <dd className="font-semibold text-[#102C54]">
                        {formatDays(confirmCard.balance_before)} &rarr;{" "}
                        <span className="text-[#2A5BDB]">
                          {formatDays(confirmCard.balance_after)}
                        </span>
                      </dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-[11px] text-textSecondary">Reason</dt>
                      <dd className="font-medium text-[#102C54]">{confirmCard.reason}</dd>
                    </div>
                  </dl>

                  {confirmCard.policy_check_reasons &&
                    confirmCard.policy_check_reasons.length > 0 && (
                      <div className="space-y-1.5">
                        <p className="text-[11px] font-bold uppercase tracking-wide text-[#102C54]">
                          Policy Rule Breakdown
                        </p>
                        <ul className="space-y-1">
                          {confirmCard.policy_check_reasons.map((item, i) => {
                            const st = (item.status || "OK").toUpperCase();
                            const tone =
                              st === "BLOCKED"
                                ? "border-rose-200 bg-rose-50 text-rose-900"
                                : st === "VIOLATION"
                                  ? "border-amber-200 bg-amber-50 text-amber-950"
                                  : "border-emerald-200 bg-emerald-50 text-emerald-900";
                            return (
                              <li
                                key={`${item.rule}-${i}`}
                                className={`flex items-start gap-2 rounded-small border px-2.5 py-1.5 text-[11px] ${tone}`}
                              >
                                {st === "BLOCKED" ? (
                                  <ShieldAlert className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                                ) : st === "VIOLATION" ? (
                                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                                ) : (
                                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                                )}
                                <div>
                                  <span className="font-bold uppercase mr-1">[{st}]</span>
                                  <span>{item.message}</span>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    )}

                  {confirmCard.policy_check_status === "VIOLATION" && (
                    <p className="text-[11px] text-amber-900 bg-amber-50 border border-amber-200 rounded-small px-3 py-2">
                      Policy violations trigger a mandatory Stage 2 HR Head exception approval
                      alongside manager approval.
                    </p>
                  )}

                  <Button
                    type="button"
                    disabled={
                      confirmCard.policy_check_status === "BLOCKED" ||
                      submitMutation.isPending ||
                      isOffline
                    }
                    onClick={() => void handleConfirmCardSubmit()}
                    className="w-full bg-[#102C54] text-white hover:bg-[#2A5BDB] text-xs"
                  >
                    {submitMutation.isPending
                      ? "Submitting Request..."
                      : confirmCard.policy_check_status === "BLOCKED"
                        ? "Blocked by Leave Policy"
                        : "Confirm & Submit Leave Request"}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      ) : (
        <Card className="border border-appBorder bg-white shadow-sm max-w-2xl">
          <CardHeader>
            <div className="flex items-center gap-2">
              <CalendarPlus className="h-4 w-4 text-[#2A5BDB]" aria-hidden="true" />
              <CardTitle className="text-base text-[#102C54]">
                Structured Leave Application
              </CardTitle>
            </div>
            <CardDescription className="text-xs text-textSecondary">
              Submit dates directly to the leave reservation ledger and manager approval workflow.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handlePlainFormSubmit} className="space-y-4 text-xs">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor={typeSelectId} className="block font-semibold text-[#102C54] mb-1">
                    Leave Type
                  </label>
                  <select
                    id={typeSelectId}
                    value={selectedLeaveTypeCode}
                    onChange={(e) => setLeaveTypeCode(e.target.value)}
                    disabled={submitMutation.isPending || isOffline}
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
                  >
                    {activeLeaveTypes.length === 0 ? (
                      <option value="annual">Annual Leave</option>
                    ) : (
                      activeLeaveTypes.map((lt) => (
                        <option key={lt.id} value={lt.code}>
                          {lt.name} ({lt.code})
                        </option>
                      ))
                    )}
                  </select>
                </div>

                <div className="flex items-end gap-4">
                  <label
                    htmlFor={halfDayId}
                    className="inline-flex items-center gap-2 font-semibold text-[#102C54] cursor-pointer pb-2"
                  >
                    <input
                      id={halfDayId}
                      type="checkbox"
                      checked={isHalfDay}
                      onChange={(e) => {
                        setIsHalfDay(e.target.checked);
                        if (e.target.checked && startDate) setEndDate(startDate);
                      }}
                      className="rounded border-appBorder accent-[#2A5BDB]"
                    />
                    Half-Day Leave (0.5 day)
                  </label>
                </div>

                <div>
                  <label htmlFor={startDateId} className="block font-semibold text-[#102C54] mb-1">
                    Start Date
                  </label>
                  <input
                    id={startDateId}
                    type="date"
                    value={startDate}
                    onChange={(e) => {
                      setStartDate(e.target.value);
                      if (isHalfDay || !endDate) setEndDate(e.target.value);
                    }}
                    required
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
                  />
                </div>

                <div>
                  <label htmlFor={endDateId} className="block font-semibold text-[#102C54] mb-1">
                    End Date
                  </label>
                  <input
                    id={endDateId}
                    type="date"
                    value={isHalfDay ? startDate : endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    disabled={isHalfDay}
                    required={!isHalfDay}
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54] disabled:bg-slate-100"
                  />
                </div>

                {isHalfDay && (
                  <div>
                    <label
                      htmlFor={halfDayPeriodId}
                      className="block font-semibold text-[#102C54] mb-1"
                    >
                      Half-Day Period
                    </label>
                    <select
                      id={halfDayPeriodId}
                      value={halfDayPeriod}
                      onChange={(e) => setHalfDayPeriod(e.target.value as "morning" | "afternoon")}
                      className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
                    >
                      <option value="morning">Morning (First Half)</option>
                      <option value="afternoon">Afternoon (Second Half)</option>
                    </select>
                  </div>
                )}

                <div className={isHalfDay ? "" : "sm:col-span-2"}>
                  <label
                    htmlFor={documentIdInput}
                    className="block font-semibold text-[#102C54] mb-1"
                  >
                    Supporting Document ID (Optional Vault UUID)
                  </label>
                  <input
                    id={documentIdInput}
                    type="text"
                    value={documentId}
                    onChange={(e) => setDocumentId(e.target.value)}
                    placeholder="Optional employee document UUID for medical leave"
                    className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
                  />
                </div>
              </div>

              <div>
                <label htmlFor={reasonId} className="block font-semibold text-[#102C54] mb-1">
                  Reason
                </label>
                <textarea
                  id={reasonId}
                  rows={3}
                  dir="auto"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  required
                  placeholder="Provide reason for leave..."
                  className="w-full rounded-small border border-slate-300 bg-white px-3 py-2 text-xs text-[#102C54]"
                />
              </div>

              <Button
                type="submit"
                disabled={submitMutation.isPending || isOffline}
                className="bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1.5 text-xs"
              >
                <Send className="h-3.5 w-3.5" aria-hidden="true" />
                {submitMutation.isPending ? "Submitting..." : "Submit Leave Request"}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
