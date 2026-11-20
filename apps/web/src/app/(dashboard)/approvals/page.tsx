"use client";

import React, { useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Calendar,
  DollarSign,
  ShieldAlert,
  User,
  Check,
  X,
  RefreshCw,
  Search,
  FileEdit,
  History,
  ArrowRight,
  MessageSquare,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StandbyBanner } from "@/components/ui/standby-banner";
import { useBackendStatus } from "@/hooks/use-backend-status";
import { useCurrentUser } from "@/hooks/use-current-user";
import {
  ApprovalItem,
  useApprovals,
  useApproveMutation,
  useRejectMutation,
  useRequestChangesMutation,
} from "@/hooks/use-approvals";
import { formatPKT } from "@/lib/format";

export default function ApprovalsPage() {
  const { data: currentUser } = useCurrentUser();
  const { isStandby: isBackendStandby } = useBackendStatus();
  const [selectedStatus, setSelectedStatus] = useState<string>("all");
  const [selectedApprovalId, setSelectedApprovalId] = useState<string | null>(null);

  // Decision state
  const [decisionMode, setDecisionMode] = useState<"approve" | "changes" | "reject" | null>(null);
  const [decisionReason, setDecisionReason] = useState<string>("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const {
    data: approvals = [],
    isLoading,
    error,
    refetch,
    isFetching,
  } = useApprovals(selectedStatus === "all" ? undefined : selectedStatus);

  const approveMutation = useApproveMutation();
  const rejectMutation = useRejectMutation();
  const requestChangesMutation = useRequestChangesMutation();

  // Find currently selected approval, default to first in list
  const activeApproval = approvals.find((a) => a.id === selectedApprovalId) || approvals[0] || null;

  // Counts for summary metrics
  const pendingCount = approvals.filter((a) => a.status === "pending").length;
  const changesCount = approvals.filter((a) => a.status === "changes_requested").length;
  const approvedCount = approvals.filter((a) => a.status === "approved").length;
  const rejectedCount = approvals.filter((a) => a.status === "rejected").length;

  const isOfflineOrError = isBackendStandby || !!error;

  const handleApprove = async (id: string) => {
    setActionError(null);
    setActionSuccess(null);
    try {
      await approveMutation.mutateAsync({ id, reason: decisionReason.trim() || undefined });
      setActionSuccess("Request successfully approved and committed to workflow.");
      setDecisionMode(null);
      setDecisionReason("");
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to approve request.");
    }
  };

  const handleRequestChanges = async (id: string) => {
    if (!decisionReason.trim() || decisionReason.trim().length < 3) {
      setActionError("Correction instructions (minimum 3 characters) are strictly required.");
      return;
    }
    setActionError(null);
    setActionSuccess(null);
    try {
      await requestChangesMutation.mutateAsync({ id, reason: decisionReason.trim() });
      setActionSuccess(
        "Changes successfully requested. Draft returned to requester for correction."
      );
      setDecisionMode(null);
      setDecisionReason("");
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to request changes.");
    }
  };

  const handleReject = async (id: string) => {
    if (!decisionReason.trim()) {
      setActionError("A non-empty rejection reason is strictly required.");
      return;
    }
    setActionError(null);
    setActionSuccess(null);
    try {
      await rejectMutation.mutateAsync({ id, reason: decisionReason.trim() });
      setActionSuccess("Request successfully rejected with reason recorded.");
      setDecisionMode(null);
      setDecisionReason("");
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to reject request.");
    }
  };

  // Helper to determine if current user has role to decide
  const canDecide = (approval: ApprovalItem): boolean => {
    if (approval.status !== "pending") return false;
    if (!currentUser) return false;
    const userRole = currentUser.role;

    // Self approval check (non-owners cannot approve own request)
    if (approval.requester_id === currentUser.user_id && userRole !== "owner") {
      return false;
    }

    if (approval.approver_role === "owner") return userRole === "owner";
    if (approval.approver_role === "manager") return userRole === "owner" || userRole === "manager";
    if (approval.approver_role === "hr_head") return currentUser.is_hr_head || userRole === "owner";
    if (approval.approver_role === "payroll_admin")
      return currentUser.is_payroll_admin || userRole === "owner";
    return false;
  };

  // Helper to determine if current user can edit and resubmit returned draft
  const canResubmit = (approval: ApprovalItem): boolean => {
    if (approval.status !== "changes_requested") return false;
    if (!currentUser) return false;
    return approval.requester_id === currentUser.user_id || currentUser.role === "owner";
  };

  const getApprovalTypeBadge = (type: string) => {
    switch (type) {
      case "leave_request":
        return (
          <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
            <Calendar className="mr-1 h-3 w-3" /> Leave Request
          </Badge>
        );
      case "candidate_offer":
        return (
          <Badge variant="outline" className="border-purple-200 bg-purple-50 text-purple-700">
            <User className="mr-1 h-3 w-3" /> Candidate Offer
          </Badge>
        );
      case "payroll_override":
        return (
          <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">
            <DollarSign className="mr-1 h-3 w-3" /> Payroll Adjustment
          </Badge>
        );
      case "policy_exception":
        return (
          <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
            <ShieldAlert className="mr-1 h-3 w-3" /> Policy Exception
          </Badge>
        );
      default:
        return <Badge variant="outline">{type}</Badge>;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "pending":
        return (
          <Badge variant="warning" className="flex items-center gap-1 font-semibold">
            <Clock className="h-3 w-3" /> Pending Review
          </Badge>
        );
      case "changes_requested":
        return (
          <Badge
            variant="outline"
            className="flex items-center gap-1 font-semibold border-amber-300 bg-amber-50 text-amber-800"
          >
            <AlertTriangle className="h-3 w-3 text-amber-600" /> Changes Requested
          </Badge>
        );
      case "approved":
        return (
          <Badge variant="success" className="flex items-center gap-1 font-semibold">
            <CheckCircle2 className="h-3 w-3" /> Approved
          </Badge>
        );
      case "rejected":
        return (
          <Badge
            variant="outline"
            className="flex items-center gap-1 font-semibold border-rose-200 bg-rose-50 text-rose-700"
          >
            <XCircle className="h-3 w-3" /> Rejected
          </Badge>
        );
      default:
        return <Badge>{status}</Badge>;
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Title & Refresh */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-navy">Approvals Inbox</h1>
          <p className="text-sm text-slate-500">
            Multi-tenant governance gate with multi-round revision tracking and 3-decision approval
            workflows.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5"
          >
            <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Read-Only Standby Banner when FastAPI is offline or errored */}
      {isOfflineOrError && (
        <StandbyBanner
          title="Approvals Inbox in Read-Only Standby"
          message={
            error && !error.message.startsWith("OFFLINE:")
              ? `Could not load approvals: ${error.message}. Governance decisions are disabled until connection is restored.`
              : "FastAPI backend is offline. Governance records are never fabricated in standby mode and decision actions are disabled."
          }
          onRetry={() => refetch()}
          isRetrying={isFetching}
        />
      )}

      {/* Summary KPI Badges */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <Card className="border-l-4 border-l-amber-500 shadow-sm">
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">
                Pending Decision
              </p>
              <h3 className="text-2xl font-bold text-navy">{pendingCount}</h3>
            </div>
            <div className="rounded-full bg-amber-50 p-2 text-amber-600">
              <Clock className="h-5 w-5" />
            </div>
          </CardContent>
        </Card>

        <Card className="border-l-4 border-l-orange-500 shadow-sm">
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">
                Changes Requested
              </p>
              <h3 className="text-2xl font-bold text-navy">{changesCount}</h3>
            </div>
            <div className="rounded-full bg-orange-50 p-2 text-orange-600">
              <AlertTriangle className="h-5 w-5" />
            </div>
          </CardContent>
        </Card>

        <Card className="border-l-4 border-l-emerald-500 shadow-sm">
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">
                Approved
              </p>
              <h3 className="text-2xl font-bold text-navy">{approvedCount}</h3>
            </div>
            <div className="rounded-full bg-emerald-50 p-2 text-emerald-600">
              <CheckCircle2 className="h-5 w-5" />
            </div>
          </CardContent>
        </Card>

        <Card className="border-l-4 border-l-rose-500 shadow-sm">
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">
                Rejected
              </p>
              <h3 className="text-2xl font-bold text-navy">{rejectedCount}</h3>
            </div>
            <div className="rounded-full bg-rose-50 p-2 text-rose-600">
              <XCircle className="h-5 w-5" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Action Notification Banners */}
      {actionSuccess && (
        <div className="flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50 p-4 text-emerald-800">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-600" />
            <span className="text-sm font-medium">{actionSuccess}</span>
          </div>
          <button
            onClick={() => setActionSuccess(null)}
            className="text-emerald-600 hover:text-emerald-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {actionError && (
        <div className="flex items-center justify-between rounded-md border border-rose-200 bg-rose-50 p-4 text-rose-800">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-rose-600" />
            <span className="text-sm font-medium">{actionError}</span>
          </div>
          <button
            onClick={() => setActionError(null)}
            className="text-rose-600 hover:text-rose-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Main Two-Pane Workflow Layout */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left Pane: Filter Tabs & Approvals List */}
        <div className="space-y-4 lg:col-span-5">
          {/* Filter Status Selector */}
          <div className="flex flex-wrap gap-1 rounded-lg border bg-slate-50/80 p-1 text-xs">
            {[
              { id: "all", label: "All" },
              { id: "pending", label: "Pending" },
              { id: "changes_requested", label: "Changes" },
              { id: "approved", label: "Approved" },
              { id: "rejected", label: "Rejected" },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setSelectedStatus(tab.id)}
                className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                  selectedStatus === tab.id
                    ? "bg-white text-navy shadow-sm border border-slate-200"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* List Cards */}
          <div className="space-y-2.5 max-h-[700px] overflow-y-auto pr-1">
            {isLoading ? (
              <div className="space-y-2.5 animate-pulse" aria-busy="true">
                {Array.from({ length: 3 }).map((_, idx) => (
                  <div key={idx} className="h-24 rounded-lg border border-slate-200 bg-slate-100" />
                ))}
              </div>
            ) : isOfflineOrError ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-8 text-center">
                <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
                <p className="mt-2 text-sm font-semibold text-navy">
                  Governance Inbox Unavailable (Standby)
                </p>
                <p className="mt-1 text-xs text-amber-900">
                  Connect the FastAPI backend to load live approvals from PostgreSQL.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => refetch()}
                  disabled={isFetching}
                  className="mt-4"
                >
                  <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isFetching ? "animate-spin" : ""}`} />
                  Retry Connection
                </Button>
              </div>
            ) : approvals.length === 0 ? (
              <div className="rounded-lg border border-dashed p-8 text-center">
                <Clock className="mx-auto h-8 w-8 text-slate-300" />
                <p className="mt-2 text-sm font-medium text-slate-600">
                  No approval requests found
                </p>
                <p className="text-xs text-slate-400">
                  Requests matching this filter will appear here.
                </p>
              </div>
            ) : (
              approvals.map((item) => {
                const isSelected = activeApproval?.id === item.id;
                return (
                  <div
                    key={item.id}
                    onClick={() => {
                      setSelectedApprovalId(item.id);
                      setDecisionMode(null);
                      setDecisionReason("");
                      setActionError(null);
                      setActionSuccess(null);
                    }}
                    className={`cursor-pointer rounded-lg border p-4 transition-all ${
                      isSelected
                        ? "border-royal-blue bg-royal-blue/5 shadow-sm"
                        : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        {getApprovalTypeBadge(item.approval_type)}
                        <h4 className="mt-2 font-semibold text-navy text-sm">
                          {item.approval_type === "leave_request" &&
                            `${item.payload?.leave_type || "Leave"} (${item.payload?.days_count || 1} days)`}
                          {item.approval_type === "candidate_offer" &&
                            `Offer: ${item.payload?.candidate_name || "Candidate"}`}
                          {item.approval_type === "payroll_override" &&
                            `Payroll: ${item.payload?.employee_name || "Employee"}`}
                          {item.approval_type === "policy_exception" &&
                            `Exception: ${item.payload?.policy_name || "Policy"}`}
                        </h4>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        {getStatusBadge(item.status)}
                        <span className="text-[11px] font-semibold text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                          Rev {item.current_revision}
                        </span>
                      </div>
                    </div>

                    <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
                      <span>By: {item.requester_name || item.requester_email || "System"}</span>
                      <span>{formatPKT(item.created_at, false)}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right Pane: Selected Approval Detail & 3-Decision Actions */}
        <div className="lg:col-span-7">
          {activeApproval ? (
            <div className="space-y-4">
              <Card className="border border-slate-200 shadow-sm">
                <CardHeader className="border-b bg-slate-50/50 pb-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {getApprovalTypeBadge(activeApproval.approval_type)}
                      {getStatusBadge(activeApproval.status)}
                      <Badge variant="outline" className="bg-white text-slate-700">
                        Round {activeApproval.current_revision}
                      </Badge>
                    </div>
                    <div className="text-xs text-slate-500">
                      ID: <span className="font-mono">{activeApproval.id.slice(0, 8)}</span>
                    </div>
                  </div>
                  <CardTitle className="mt-2 text-xl text-navy">
                    {activeApproval.approval_type === "leave_request" && "Employee Leave Approval"}
                    {activeApproval.approval_type === "candidate_offer" &&
                      "Candidate Job Offer Approval"}
                    {activeApproval.approval_type === "payroll_override" &&
                      "Discretionary Payroll Override"}
                    {activeApproval.approval_type === "policy_exception" &&
                      "Corporate Policy Exception"}
                  </CardTitle>
                  <CardDescription>
                    Submitted on {formatPKT(activeApproval.created_at)} by{" "}
                    <span className="font-medium text-slate-700">
                      {activeApproval.requester_name || activeApproval.requester_email}
                    </span>
                  </CardDescription>
                </CardHeader>

                <CardContent className="space-y-6 pt-5">
                  {/* Alert Banner if Changes Requested */}
                  {activeApproval.status === "changes_requested" && (
                    <div className="rounded-lg border border-amber-300 bg-amber-50/80 p-4 text-amber-900">
                      <div className="flex items-start gap-3">
                        <AlertTriangle className="h-5 w-5 text-amber-600 mt-0.5 flex-shrink-0" />
                        <div className="space-y-2 flex-grow">
                          <h4 className="font-semibold text-sm text-amber-900">
                            Corrections Requested by {activeApproval.decider_name || "Approver"}
                          </h4>
                          <p className="text-xs text-amber-800 whitespace-pre-wrap bg-white/70 p-2.5 rounded border border-amber-200">
                            {activeApproval.decision_reason ||
                              "Please review and revise the submitted information."}
                          </p>

                          {/* CTA for requester to edit & resubmit */}
                          {canResubmit(activeApproval) && (
                            <div className="pt-2">
                              <Link href={`/approvals/${activeApproval.id}/correct`}>
                                <Button
                                  size="sm"
                                  className="bg-royal-blue text-white hover:bg-navy flex items-center gap-1.5"
                                >
                                  <FileEdit className="h-4 w-4" />
                                  Edit & Resubmit Draft (Rev {activeApproval.current_revision + 1})
                                  <ArrowRight className="h-3.5 w-3.5" />
                                </Button>
                              </Link>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Domain Payload Viewer */}
                  <div>
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
                      Current Request Details (Round {activeApproval.current_revision})
                    </h4>
                    <div className="rounded-lg border border-slate-200 bg-slate-50/50 p-4 space-y-3">
                      {activeApproval.approval_type === "leave_request" && (
                        <div className="grid grid-cols-2 gap-4 text-sm">
                          <div>
                            <span className="text-xs text-slate-500">Leave Type:</span>
                            <p className="font-medium text-navy capitalize">
                              {activeApproval.payload?.leave_type}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Duration:</span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.days_count} business days
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Start Date:</span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.start_date}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">End Date:</span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.end_date}
                            </p>
                          </div>
                          <div className="col-span-2">
                            <span className="text-xs text-slate-500">Reason / Details:</span>
                            <p className="font-medium text-navy mt-1">
                              {activeApproval.payload?.reason}
                            </p>
                          </div>
                        </div>
                      )}

                      {activeApproval.approval_type === "candidate_offer" && (
                        <div className="grid grid-cols-2 gap-4 text-sm">
                          <div>
                            <span className="text-xs text-slate-500">Candidate Name:</span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.candidate_name}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Target Position:</span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.position}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Candidate Email:</span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.candidate_email}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Offered Monthly Salary:</span>
                            <p className="font-medium text-navy font-semibold text-royal-blue">
                              PKR{" "}
                              {Number(
                                activeApproval.payload?.offered_salary_pkr || 0
                              ).toLocaleString()}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">
                              Anticipated Joining Date:
                            </span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.start_date}
                            </p>
                          </div>
                        </div>
                      )}

                      {activeApproval.approval_type === "payroll_override" && (
                        <div className="grid grid-cols-2 gap-4 text-sm">
                          <div>
                            <span className="text-xs text-slate-500">Target Employee:</span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.employee_name}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Employee ID:</span>
                            <p className="font-medium text-navy font-mono">
                              {activeApproval.payload?.employee_id}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Discretionary Bonus:</span>
                            <p className="font-medium text-emerald-600 font-semibold">
                              + PKR{" "}
                              {Number(activeApproval.payload?.bonus_pkr || 0).toLocaleString()}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Deduction:</span>
                            <p className="font-medium text-rose-600 font-semibold">
                              - PKR{" "}
                              {Number(activeApproval.payload?.deduction_pkr || 0).toLocaleString()}
                            </p>
                          </div>
                          <div className="col-span-2">
                            <span className="text-xs text-slate-500">
                              Calculated Net Adjustment:
                            </span>
                            <p className="font-medium text-royal-blue font-bold">
                              {Number(activeApproval.payload?.net_adjustment_pkr || 0) >= 0
                                ? "+"
                                : ""}
                              PKR{" "}
                              {Number(
                                activeApproval.payload?.net_adjustment_pkr || 0
                              ).toLocaleString()}
                            </p>
                          </div>
                          <div className="col-span-2">
                            <span className="text-xs text-slate-500">Audit Justification:</span>
                            <p className="font-medium text-navy mt-1">
                              {activeApproval.payload?.justification}
                            </p>
                          </div>
                        </div>
                      )}

                      {activeApproval.approval_type === "policy_exception" && (
                        <div className="space-y-3 text-sm">
                          <div>
                            <span className="text-xs text-slate-500">Policy Name:</span>
                            <p className="font-medium text-navy">
                              {activeApproval.payload?.policy_name}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Exception Description:</span>
                            <p className="font-medium text-navy mt-1">
                              {activeApproval.payload?.exception_description}
                            </p>
                          </div>
                          <div>
                            <span className="text-xs text-slate-500">Impact Assessment:</span>
                            <p className="font-medium text-navy mt-1">
                              {activeApproval.payload?.impact_assessment}
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Revision & Decision History Timeline */}
                  <div>
                    <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
                      <History className="h-3.5 w-3.5" />
                      Revision & Decision History ({activeApproval.revisions?.length || 1} Round
                      {activeApproval.revisions?.length === 1 ? "" : "s"})
                    </div>
                    <div className="space-y-3">
                      {(activeApproval.revisions && activeApproval.revisions.length > 0
                        ? activeApproval.revisions
                        : [
                            {
                              id: activeApproval.id,
                              revision_number: 1,
                              submitted_at: activeApproval.created_at,
                              submitter_name: activeApproval.requester_name,
                              decision: activeApproval.status,
                              decision_reason: activeApproval.decision_reason,
                              decider_name: activeApproval.decider_name,
                              decided_at: activeApproval.decided_at,
                            },
                          ]
                      ).map((rev: any) => (
                        <div
                          key={rev.id || rev.revision_number}
                          className="rounded-lg border border-slate-200 bg-white p-3 text-xs space-y-1.5 shadow-xs"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-bold text-navy">Round {rev.revision_number}</span>
                            <span className="text-slate-400">
                              {formatPKT(rev.submitted_at || activeApproval.created_at)}
                            </span>
                          </div>
                          <div className="text-slate-600">
                            Submitted by:{" "}
                            <span className="font-medium text-slate-800">
                              {rev.submitter_name || activeApproval.requester_name || "Requester"}
                            </span>
                          </div>
                          {rev.decision && (
                            <div className="mt-2 rounded bg-slate-50 p-2 border border-slate-100">
                              <div className="flex items-center justify-between">
                                <span className="font-semibold text-slate-700 capitalize">
                                  Outcome: {rev.decision.replace("_", " ")}
                                </span>
                                <span className="text-slate-400">
                                  {rev.decided_at ? formatPKT(rev.decided_at) : ""}
                                </span>
                              </div>
                              {rev.decider_name && (
                                <p className="text-slate-500 mt-0.5">By: {rev.decider_name}</p>
                              )}
                              {rev.decision_reason && (
                                <p className="text-slate-700 mt-1 italic font-sans bg-white p-1.5 rounded border border-slate-200">
                                  &ldquo;{rev.decision_reason}&rdquo;
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* 3-Decision Approver Controls (Only shown for pending requests) */}
                  {activeApproval.status === "pending" && (
                    <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-4 shadow-sm">
                      <div className="flex items-center justify-between">
                        <h4 className="font-semibold text-sm text-navy">
                          Approver Governance Decision
                        </h4>
                        <span className="text-xs text-slate-500">
                          Required Role:{" "}
                          <Badge variant="outline" className="font-mono uppercase">
                            {activeApproval.approver_role}
                          </Badge>
                        </span>
                      </div>

                      {canDecide(activeApproval) ? (
                        <>
                          {/* 3 Decision Choice Buttons */}
                          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                            <Button
                              disabled={isOfflineOrError}
                              onClick={() => {
                                setDecisionMode("approve");
                                setDecisionReason("");
                              }}
                              className={`flex items-center justify-center gap-1.5 ${
                                decisionMode === "approve"
                                  ? "bg-emerald-700 text-white"
                                  : "bg-emerald-600 text-white hover:bg-emerald-700"
                              }`}
                            >
                              <Check className="h-4 w-4" />
                              Approve
                            </Button>

                            <Button
                              disabled={isOfflineOrError}
                              onClick={() => {
                                setDecisionMode("changes");
                                setDecisionReason("");
                              }}
                              className={`flex items-center justify-center gap-1.5 ${
                                decisionMode === "changes"
                                  ? "bg-amber-600 text-white"
                                  : "bg-amber-500 text-white hover:bg-amber-600"
                              }`}
                            >
                              <MessageSquare className="h-4 w-4" />
                              Request Changes
                            </Button>

                            <Button
                              disabled={isOfflineOrError}
                              onClick={() => {
                                setDecisionMode("reject");
                                setDecisionReason("");
                              }}
                              className={`flex items-center justify-center gap-1.5 ${
                                decisionMode === "reject"
                                  ? "bg-rose-700 text-white"
                                  : "bg-rose-600 text-white hover:bg-rose-700"
                              }`}
                            >
                              <X className="h-4 w-4" />
                              Reject
                            </Button>
                          </div>

                          {/* Decision Form Prompt */}
                          {decisionMode && (
                            <div className="rounded-md border p-4 bg-slate-50 space-y-3">
                              <label className="text-xs font-semibold text-slate-700">
                                {decisionMode === "approve" && "Approval Remarks (Optional):"}
                                {decisionMode === "changes" &&
                                  "Correction Instructions (Required):"}
                                {decisionMode === "reject" && "Rejection Reason (Required):"}
                              </label>

                              <textarea
                                value={decisionReason}
                                onChange={(e) => setDecisionReason(e.target.value)}
                                placeholder={
                                  decisionMode === "approve"
                                    ? "Add optional comments for the applicant..."
                                    : decisionMode === "changes"
                                      ? "Specify what changes or corrections must be made before this can be approved..."
                                      : "Explain why this request is being permanently rejected..."
                                }
                                rows={3}
                                className="w-full rounded-md border border-slate-300 p-2.5 text-xs text-slate-900 focus:border-royal-blue focus:outline-none"
                              />

                              <div className="flex justify-end gap-2">
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => {
                                    setDecisionMode(null);
                                    setDecisionReason("");
                                  }}
                                >
                                  Cancel
                                </Button>

                                {decisionMode === "approve" && (
                                  <Button
                                    size="sm"
                                    onClick={() => handleApprove(activeApproval.id)}
                                    disabled={approveMutation.isPending}
                                    className="bg-emerald-600 text-white hover:bg-emerald-700"
                                  >
                                    Confirm Approval
                                  </Button>
                                )}

                                {decisionMode === "changes" && (
                                  <Button
                                    size="sm"
                                    onClick={() => handleRequestChanges(activeApproval.id)}
                                    disabled={
                                      requestChangesMutation.isPending || !decisionReason.trim()
                                    }
                                    className="bg-amber-600 text-white hover:bg-amber-700"
                                  >
                                    Submit Change Request
                                  </Button>
                                )}

                                {decisionMode === "reject" && (
                                  <Button
                                    size="sm"
                                    onClick={() => handleReject(activeApproval.id)}
                                    disabled={rejectMutation.isPending || !decisionReason.trim()}
                                    className="bg-rose-600 text-white hover:bg-rose-700"
                                  >
                                    Confirm Permanent Rejection
                                  </Button>
                                )}
                              </div>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="rounded border border-dashed border-slate-200 bg-slate-50/50 p-4 text-center text-xs text-slate-500">
                          {activeApproval.requester_id === currentUser?.user_id &&
                          currentUser?.role !== "owner"
                            ? "Anti-Self-Approval Rule: You submitted this request and cannot review your own submission."
                            : `Review requires '${activeApproval.approver_role}' role or elevated permission.`}
                        </div>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          ) : (
            <div className="flex h-64 items-center justify-center rounded-lg border border-dashed p-8 text-center text-slate-500">
              Select an approval request from the left inbox to view details.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
