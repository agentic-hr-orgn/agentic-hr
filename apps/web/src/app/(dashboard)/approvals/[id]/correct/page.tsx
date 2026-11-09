"use client";

import React, { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  AlertTriangle,
  FileEdit,
  Save,
  Clock,
  DollarSign,
  Calendar,
  User,
  ShieldAlert,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useApprovalDetails, useResubmitMutation } from "@/hooks/use-approvals";

export default function ApprovalCorrectionPage() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id as string;

  const { data: approval, isLoading, error } = useApprovalDetails(id);
  const resubmitMutation = useResubmitMutation();

  const [formData, setFormData] = useState<Record<string, any>>({});
  const [formError, setFormError] = useState<string | null>(null);

  // Initialize form state once data is loaded
  useEffect(() => {
    if (approval?.payload) {
      setFormData({ ...approval.payload });
    }
  }, [approval]);

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center p-8 text-sm text-slate-500">
        Loading approval draft for correction...
      </div>
    );
  }

  if (error || !approval) {
    return (
      <div className="space-y-4 p-8 text-center">
        <p className="text-sm font-semibold text-rose-600">Failed to load approval request.</p>
        <Link href="/approvals">
          <Button variant="outline" size="sm">
            <ArrowLeft className="mr-1.5 h-4 w-4" /> Back to Approvals
          </Button>
        </Link>
      </div>
    );
  }

  // Pre-calculated payroll net adjustment
  const bonus = Number(formData.bonus_pkr || 0);
  const deduction = Number(formData.deduction_pkr || 0);
  const calculatedNetPayroll = bonus - deduction;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    // Basic domain validation
    if (approval.approval_type === "leave_request") {
      if (formData.start_date && formData.end_date && formData.end_date < formData.start_date) {
        setFormError("End date must be greater than or equal to start date.");
        return;
      }
      if (!formData.reason || formData.reason.trim().length < 3) {
        setFormError("Leave reason is required (minimum 3 characters).");
        return;
      }
    } else if (approval.approval_type === "payroll_override") {
      if (bonus === 0 && deduction === 0) {
        setFormError("At least one non-zero bonus or deduction amount must be specified.");
        return;
      }
      if (!formData.justification || formData.justification.trim().length < 5) {
        setFormError("Audit justification is required (minimum 5 characters).");
        return;
      }
    } else if (approval.approval_type === "candidate_offer") {
      if (Number(formData.offered_salary_pkr || 0) <= 0) {
        setFormError("Offered salary must be greater than zero.");
        return;
      }
    }

    try {
      await resubmitMutation.mutateAsync({
        id: approval.id,
        payload: formData,
      });
      router.push("/approvals");
    } catch (err: any) {
      setFormError(err.message || "Failed to resubmit corrected draft.");
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-12">
      {/* Top Navigation */}
      <div className="flex items-center gap-2">
        <Link href="/approvals">
          <Button variant="outline" size="sm" className="flex items-center gap-1.5 text-xs">
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to Approvals Inbox
          </Button>
        </Link>
      </div>

      {/* Page Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Badge variant="outline" className="bg-amber-50 text-amber-800 border-amber-300">
            Round {approval.current_revision + 1} Draft
          </Badge>
          <span className="text-xs text-slate-500 font-mono">ID: {approval.id.slice(0, 8)}</span>
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-navy">
          Correct & Resubmit Approval Request
        </h1>
        <p className="text-sm text-slate-500">
          Amend the returned draft according to the reviewer&apos;s instructions and resubmit for
          renewed governance review.
        </p>
      </div>

      {/* Reviewer Feedback Callout */}
      <div className="rounded-lg border border-amber-300 bg-amber-50/90 p-5 shadow-xs">
        <div className="flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 text-amber-600 mt-0.5 flex-shrink-0" />
          <div className="space-y-1.5 flex-grow">
            <h4 className="font-semibold text-sm text-amber-950">
              Reviewer Correction Instructions (from {approval.decider_name || "Approver"})
            </h4>
            <div className="rounded-md border border-amber-200 bg-white p-3 text-xs text-amber-900 whitespace-pre-wrap font-sans">
              {approval.decision_reason ||
                "Please review and revise the requested fields before resubmitting."}
            </div>
            <p className="text-[11px] text-amber-700">
              Your resubmission will advance this request to{" "}
              <span className="font-semibold">Round {approval.current_revision + 1}</span> and
              notify the assigned approver ({approval.approver_role}).
            </p>
          </div>
        </div>
      </div>

      {/* Error Banner */}
      {formError && (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-4 text-xs font-semibold text-rose-800">
          {formError}
        </div>
      )}

      {/* Correction Form */}
      <form onSubmit={handleSubmit}>
        <Card className="border border-slate-200 shadow-sm">
          <CardHeader className="border-b bg-slate-50/50 pb-4">
            <CardTitle className="text-base text-navy flex items-center gap-2">
              <FileEdit className="h-4 w-4 text-royal-blue" />
              Edit Request Parameters
            </CardTitle>
            <CardDescription className="text-xs">
              All modifications are strictly audited and compared against previous round snapshots.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-5 pt-5">
            {/* 1. Leave Request Fields */}
            {approval.approval_type === "leave_request" && (
              <div className="space-y-4">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className="text-xs font-semibold text-slate-700">Leave Category</label>
                    <select
                      value={formData.leave_type || "annual"}
                      onChange={(e) => setFormData({ ...formData, leave_type: e.target.value })}
                      className="mt-1 w-full rounded-md border border-slate-300 bg-white p-2 text-xs text-slate-900"
                    >
                      <option value="annual">Annual Leave</option>
                      <option value="casual">Casual Leave</option>
                      <option value="sick">Sick Leave</option>
                      <option value="maternity">Maternity Leave</option>
                      <option value="paternity">Paternity Leave</option>
                      <option value="unpaid">Unpaid Leave</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">Duration (Days)</label>
                    <input
                      type="number"
                      step="0.5"
                      min="0.5"
                      max="90"
                      value={formData.days_count || 1}
                      onChange={(e) =>
                        setFormData({ ...formData, days_count: parseFloat(e.target.value) || 1 })
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">Start Date</label>
                    <input
                      type="date"
                      value={formData.start_date || ""}
                      onChange={(e) => setFormData({ ...formData, start_date: e.target.value })}
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                      required
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">End Date</label>
                    <input
                      type="date"
                      value={formData.end_date || ""}
                      onChange={(e) => setFormData({ ...formData, end_date: e.target.value })}
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                      required
                    />
                  </div>
                </div>

                <div>
                  <label className="text-xs font-semibold text-slate-700">
                    Reason / Justification
                  </label>
                  <textarea
                    rows={3}
                    value={formData.reason || ""}
                    onChange={(e) => setFormData({ ...formData, reason: e.target.value })}
                    className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                    required
                  />
                </div>
              </div>
            )}

            {/* 2. Candidate Offer Fields */}
            {approval.approval_type === "candidate_offer" && (
              <div className="space-y-4">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className="text-xs font-semibold text-slate-700">Candidate Name</label>
                    <input
                      type="text"
                      value={formData.candidate_name || ""}
                      onChange={(e) => setFormData({ ...formData, candidate_name: e.target.value })}
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                      required
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">Candidate Email</label>
                    <input
                      type="email"
                      value={formData.candidate_email || ""}
                      onChange={(e) =>
                        setFormData({ ...formData, candidate_email: e.target.value })
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                      required
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">
                      Proposed Position
                    </label>
                    <input
                      type="text"
                      value={formData.position || ""}
                      onChange={(e) => setFormData({ ...formData, position: e.target.value })}
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                      required
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">
                      Offered Monthly Salary (PKR)
                    </label>
                    <input
                      type="number"
                      min="1"
                      value={formData.offered_salary_pkr || 0}
                      onChange={(e) =>
                        setFormData({
                          ...formData,
                          offered_salary_pkr: parseInt(e.target.value, 10) || 0,
                        })
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900 font-semibold"
                      required
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">
                      Anticipated Joining Date
                    </label>
                    <input
                      type="date"
                      value={formData.start_date || ""}
                      onChange={(e) => setFormData({ ...formData, start_date: e.target.value })}
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                      required
                    />
                  </div>
                </div>
              </div>
            )}

            {/* 3. Payroll Override Fields with Live Recalculation */}
            {approval.approval_type === "payroll_override" && (
              <div className="space-y-4">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className="text-xs font-semibold text-slate-700">Employee Name</label>
                    <input
                      type="text"
                      value={formData.employee_name || ""}
                      onChange={(e) => setFormData({ ...formData, employee_name: e.target.value })}
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                      required
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">Employee ID</label>
                    <input
                      type="text"
                      value={formData.employee_id || ""}
                      onChange={(e) => setFormData({ ...formData, employee_id: e.target.value })}
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900 font-mono"
                      required
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">
                      Discretionary Bonus (PKR)
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={formData.bonus_pkr || 0}
                      onChange={(e) =>
                        setFormData({ ...formData, bonus_pkr: parseInt(e.target.value, 10) || 0 })
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900 text-emerald-700 font-semibold"
                    />
                  </div>

                  <div>
                    <label className="text-xs font-semibold text-slate-700">
                      Payroll Deduction (PKR)
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={formData.deduction_pkr || 0}
                      onChange={(e) =>
                        setFormData({
                          ...formData,
                          deduction_pkr: parseInt(e.target.value, 10) || 0,
                        })
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900 text-rose-700 font-semibold"
                    />
                  </div>
                </div>

                {/* Real-time Recalculation Callout */}
                <div className="rounded-md border border-royal-blue/20 bg-royal-blue/5 p-3 flex items-center justify-between">
                  <div>
                    <span className="text-xs font-medium text-slate-600">
                      Deterministic Net Payroll Adjustment:
                    </span>
                    <p className="text-sm font-bold text-navy">
                      {calculatedNetPayroll >= 0 ? "+" : ""}
                      PKR {calculatedNetPayroll.toLocaleString()}
                    </p>
                  </div>
                  <Badge
                    variant="outline"
                    className="bg-white text-royal-blue border-royal-blue/30 text-xs"
                  >
                    Auto-Calculated
                  </Badge>
                </div>

                <div>
                  <label className="text-xs font-semibold text-slate-700">
                    Audit Justification
                  </label>
                  <textarea
                    rows={3}
                    value={formData.justification || ""}
                    onChange={(e) => setFormData({ ...formData, justification: e.target.value })}
                    className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                    required
                  />
                </div>
              </div>
            )}

            {/* 4. Policy Exception Fields */}
            {approval.approval_type === "policy_exception" && (
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-semibold text-slate-700">Policy Name</label>
                  <input
                    type="text"
                    value={formData.policy_name || ""}
                    onChange={(e) => setFormData({ ...formData, policy_name: e.target.value })}
                    className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                    required
                  />
                </div>

                <div>
                  <label className="text-xs font-semibold text-slate-700">
                    Exception Description
                  </label>
                  <textarea
                    rows={3}
                    value={formData.exception_description || ""}
                    onChange={(e) =>
                      setFormData({ ...formData, exception_description: e.target.value })
                    }
                    className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                    required
                  />
                </div>

                <div>
                  <label className="text-xs font-semibold text-slate-700">Impact Assessment</label>
                  <textarea
                    rows={3}
                    value={formData.impact_assessment || ""}
                    onChange={(e) =>
                      setFormData({ ...formData, impact_assessment: e.target.value })
                    }
                    className="mt-1 w-full rounded-md border border-slate-300 p-2 text-xs text-slate-900"
                    required
                  />
                </div>
              </div>
            )}

            {/* Submission Actions */}
            <div className="flex items-center justify-end gap-3 pt-4 border-t">
              <Link href="/approvals">
                <Button variant="outline" size="sm" type="button">
                  Cancel
                </Button>
              </Link>

              <Button
                type="submit"
                size="sm"
                disabled={resubmitMutation.isPending}
                className="bg-royal-blue text-white hover:bg-navy flex items-center gap-1.5"
              >
                <Save className="h-4 w-4" />
                {resubmitMutation.isPending
                  ? "Submitting Revision..."
                  : `Resubmit Draft (Round ${approval.current_revision + 1})`}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}
