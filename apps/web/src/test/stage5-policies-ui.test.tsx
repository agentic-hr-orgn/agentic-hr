import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type {
  PolicyDocumentItem,
  PolicyPreviewResponse,
  PolicyReviewQueueItem,
  PolicySearchResponse,
} from "@agentic-hr/contracts";
import PoliciesPage from "@/app/(dashboard)/policies/page";
import PolicyPreviewPage from "@/app/(dashboard)/policies/[id]/preview/page";
import { resolveRouteTitle } from "@/components/layout/header";
import { Step2UploadPolicies } from "@/components/onboarding/step2-upload-policies";
import { ToastProvider } from "@/components/ui/toast-provider";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as currentUserHook from "@/hooks/use-current-user";
import * as policiesHook from "@/hooks/use-policies";
import { ApiHttpError, ApiOfflineError } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

const mockParamsId = vi.fn<() => string>(() => "pol-v3");

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: mockParamsId() }),
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/policies",
}));

vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-policies");

function makePolicyDoc(overrides: Partial<PolicyDocumentItem> = {}): PolicyDocumentItem {
  return {
    id: "pol-1",
    tenant_id: "t-acme",
    title: "Leave & Attendance Handbook",
    doc_type: "company_policy",
    visibility: "all",
    version: 1,
    file_name: "leave_v1.pdf",
    mime_type: "application/pdf",
    size_bytes: 4096,
    storage_provider: "local",
    storage_key: "t-acme/policies/leave_v1.pdf",
    ingestion_status: "indexed",
    lifecycle_status: "draft",
    display_status: "READY_TO_PUBLISH",
    effective_date: "2026-01-01",
    source: "upload",
    supersedes_document_id: null,
    approval_id: null,
    page_count: 2,
    progress_pct: 100,
    progress_stage: "completed",
    error_message: null,
    low_confidence_pages: [],
    has_prompt_injection_warning: false,
    uploaded_by: "u-owner",
    created_at: "2026-11-20T09:00:00+05:00",
    updated_at: "2026-11-20T09:05:00+05:00",
    ...overrides,
  };
}

describe("Stage 5 Prompt 6 — Policy Library, Bilingual Search, Preview/Fix & Step 2 UI", () => {
  const uploadMutateAsync = vi.fn();
  const deleteMutateAsync = vi.fn();
  const publishMutateAsync = vi.fn();
  const reingestMutateAsync = vi.fn();
  const searchMutateAsync = vi.fn();
  const fixPageMutateAsync = vi.fn();
  const fixChunkMutateAsync = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockParamsId.mockReturnValue("pol-v3");
    act(() => {
      useToastStore.getState().clearToasts();
    });

    vi.spyOn(backendStatusHook, "useBackendStatus").mockReturnValue({
      status: "live",
      health: null,
      isLive: true,
      isStandby: false,
      isChecking: false,
      useFakeIntegrations: true,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      data: {
        user_id: "u-owner",
        clerk_user_id: "clerk-owner",
        email: "owner@acme.internal",
        name: "Acme Owner",
        tenant_id: "t-acme",
        clerk_org_id: "org_acme",
        tenant_name: "Acme Tech Solutions",
        role: "owner",
        is_hr_head: true,
        is_payroll_admin: true,
        permissions: ["owner"],
      },
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(policiesHook, "usePolicies").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    vi.spyOn(policiesHook, "usePolicyReviewQueue").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(policiesHook, "usePolicyPreview").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    vi.spyOn(policiesHook, "useUploadPolicy").mockReturnValue({
      mutateAsync: uploadMutateAsync,
      isPending: false,
    } as any);

    vi.spyOn(policiesHook, "useDeletePolicy").mockReturnValue({
      mutateAsync: deleteMutateAsync,
      isPending: false,
    } as any);

    vi.spyOn(policiesHook, "usePublishPolicy").mockReturnValue({
      mutateAsync: publishMutateAsync,
      isPending: false,
    } as any);

    vi.spyOn(policiesHook, "useReingestPolicy").mockReturnValue({
      mutateAsync: reingestMutateAsync,
      isPending: false,
    } as any);

    vi.spyOn(policiesHook, "useSearchPolicies").mockReturnValue({
      mutateAsync: searchMutateAsync,
      isPending: false,
      data: undefined,
      error: null,
    } as any);

    vi.spyOn(policiesHook, "useFixPolicyPage").mockReturnValue({
      mutateAsync: fixPageMutateAsync,
      isPending: false,
    } as any);

    vi.spyOn(policiesHook, "useFixPolicyChunk").mockReturnValue({
      mutateAsync: fixChunkMutateAsync,
      isPending: false,
    } as any);
  });

  it("renders policy versions with dual statuses, submits metadata without empty strings, and triggers Submit for Publish approval toast", async () => {
    const v1Superseded = makePolicyDoc({
      id: "pol-v1",
      title: "Leave Policy",
      version: 1,
      lifecycle_status: "superseded",
      ingestion_status: "indexed",
      display_status: "SUPERSEDED",
    });
    const v2Active = makePolicyDoc({
      id: "pol-v2",
      title: "Leave Policy",
      version: 2,
      lifecycle_status: "active",
      ingestion_status: "indexed",
      display_status: "ACTIVE",
    });
    const v3Draft = makePolicyDoc({
      id: "pol-v3",
      title: "Leave Policy",
      version: 3,
      doc_type: "labour_law",
      visibility: "hr_only",
      lifecycle_status: "draft",
      ingestion_status: "indexed",
      display_status: "READY_TO_PUBLISH",
    });

    vi.spyOn(policiesHook, "usePolicies").mockReturnValue({
      data: [v1Superseded, v2Active, v3Draft],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    uploadMutateAsync.mockResolvedValueOnce(
      makePolicyDoc({
        id: "pol-ur",
        title: "Punjab Shops Act Notice",
        doc_type: "labour_law",
        visibility: "hr_only",
        version: 1,
        ingestion_status: "indexed",
        lifecycle_status: "draft",
      })
    );

    publishMutateAsync.mockResolvedValueOnce({
      document: { ...v3Draft, lifecycle_status: "review", display_status: "PENDING_APPROVAL" },
      approval_id: "apr-policy-1",
      approval_status: "pending",
    });

    render(
      <>
        <PoliciesPage />
        <ToastProvider />
      </>
    );

    expect(screen.getByText(/Corporate Policy Library \(3\)/i)).toBeInTheDocument();
    const policyTable = within(screen.getByRole("table"));
    expect(policyTable.getByText("v1")).toBeInTheDocument();
    expect(policyTable.getByText("v2")).toBeInTheDocument();
    expect(policyTable.getByText("v3")).toBeInTheDocument();
    expect(policyTable.getByText("Superseded")).toBeInTheDocument();
    expect(policyTable.getByText("Active")).toBeInTheDocument();
    expect(policyTable.getByText("Draft")).toBeInTheDocument();

    // Only the indexed draft row exposes "Submit for Publish"
    const publishButtons = screen.getAllByRole("button", { name: /Submit for Publish/i });
    expect(publishButtons).toHaveLength(1);

    fireEvent.click(publishButtons[0]);
    await waitFor(() => {
      expect(publishMutateAsync).toHaveBeenCalledWith({
        policyId: "pol-v3",
        payload: { approver_role: "owner" },
      });
    });

    const approvalsLink = await screen.findByRole("link", { name: /Open Approvals Inbox/i });
    expect(approvalsLink).toHaveAttribute("href", "/approvals");

    // Upload form metadata submission without blank fields
    const form = screen.getByRole("form", { name: /Upload Policy Document/i });
    const scoped = within(form);
    fireEvent.change(scoped.getByLabelText(/Policy Title/i), {
      target: { value: "Punjab Shops Act Notice" },
    });
    fireEvent.change(scoped.getByLabelText(/Document Type/i), {
      target: { value: "labour_law" },
    });
    fireEvent.change(scoped.getByLabelText(/Visibility/i), {
      target: { value: "hr_only" },
    });
    fireEvent.change(scoped.getByLabelText(/Language/i), {
      target: { value: "ur" },
    });
    fireEvent.change(scoped.getByLabelText(/Effective Date/i), {
      target: { value: "2026-06-01" },
    });
    fireEvent.change(scoped.getByLabelText(/Source/i), {
      target: { value: "statutory" },
    });
    const txtFile = new File(["Annual leave rules"], "shops_act.txt", { type: "text/plain" });
    fireEvent.change(scoped.getByLabelText(/Document File/i), {
      target: { files: [txtFile] },
    });
    fireEvent.click(scoped.getByRole("button", { name: /^Upload Policy$/i }));

    await waitFor(() => {
      expect(uploadMutateAsync).toHaveBeenCalledWith({
        file: txtFile,
        title: "Punjab Shops Act Notice",
        docType: "labour_law",
        visibility: "hr_only",
        language: "ur",
        effectiveDate: "2026-06-01",
        source: "statutory",
      });
    });
  });

  it("supports bilingual Urdu & English search with Stage 7 Citation metadata, dir=auto, and prompt-injection flags", async () => {
    const searchResponse: PolicySearchResponse = {
      query: "کیا سالانہ چھٹیاں اگلے سال منتقل ہو سکتی ہیں؟",
      normalized_query: "کیا سالانہ چھٹیاں اگلے سال منتقل ہو سکتی ہیں؟",
      query_language: "ur",
      total_candidates: 3,
      embedding_model: "gemini-embedding-001",
      embedding_dim: 768,
      hits: [
        {
          citation: {
            chunk_id: "chk-ur-1",
            document_id: "pol-leave-v2",
            title: "Leave & Attendance Policy",
            version: 2,
            doc_type: "company_policy",
            visibility: "all",
            section_path: "Section 4 > Carry Forward",
            page_start: 2,
            page_end: 3,
            page_range: "pp. 2-3",
            language: "ur",
          },
          text: "ملازمین زیادہ سے زیادہ ۱۰ سالانہ چھٹیاں اگلے سال منتقل کر سکتے ہیں۔",
          snippet: "ملازمین زیادہ سے زیادہ ۱۰ سالانہ چھٹیاں اگلے سال منتقل کر سکتے ہیں۔",
          score: 0.91,
          rrf_score: 0.042,
          vector_rank: 1,
          fts_rank: 1,
          trgm_rank: 2,
          has_injection_flag: true,
        },
      ],
    };

    searchMutateAsync.mockResolvedValueOnce(searchResponse);
    vi.spyOn(policiesHook, "useSearchPolicies").mockReturnValue({
      mutateAsync: searchMutateAsync,
      isPending: false,
      data: searchResponse,
      error: null,
    } as any);

    render(<PoliciesPage />);

    const searchTab = screen.getByRole("tab", { name: /Bilingual Search/i });
    fireEvent.click(searchTab);

    expect(
      screen.getByText(/Searching active policies visible to your role \(Owner\)/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Retrieval preview — cited Q&A arrives in Stage 7/i)
    ).toBeInTheDocument();

    const searchInput = screen.getByRole("searchbox");
    expect(searchInput).toHaveAttribute("dir", "auto");

    fireEvent.change(searchInput, {
      target: { value: "کیا سالانہ چھٹیاں اگلے سال منتقل ہو سکتی ہیں؟" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Urdu \(UR\)$/i }));
    fireEvent.click(screen.getByRole("button", { name: /Search Policies/i }));

    await waitFor(() => {
      expect(searchMutateAsync).toHaveBeenCalledWith({
        query: "کیا سالانہ چھٹیاں اگلے سال منتقل ہو سکتی ہیں؟",
        top_k: 8,
        doc_type: null,
        language: "ur",
      });
    });

    // Stage 7 Citation metadata & RTL dir="auto" snippet & injection chip
    expect(screen.getByText("Leave & Attendance Policy")).toBeInTheDocument();
    expect(screen.getByText("Section 4 > Carry Forward • pp. 2-3")).toBeInTheDocument();
    expect(screen.getByText("Score: 91%")).toBeInTheDocument();
    expect(screen.getByText(/Prompt-Injection Flagged \(Untrusted Text\)/i)).toBeInTheDocument();

    const snippetEl = screen.getByText(
      /ملازمین زیادہ سے زیادہ ۱۰ سالانہ چھٹیاں اگلے سال منتقل کر سکتے ہیں/i
    );
    expect(snippetEl).toHaveAttribute("dir", "auto");
  });

  it("gates Publish Approval on Preview page until indexed and supports inline page fix & chunk edit", async () => {
    const needsReviewPreview: PolicyPreviewResponse = {
      document: makePolicyDoc({
        id: "pol-v3",
        title: "Employee Handbook 2026",
        version: 3,
        ingestion_status: "needs_review",
        lifecycle_status: "draft",
        display_status: "NEEDS_REVIEW",
      }),
      pages: [
        {
          id: "pg-2",
          page_number: 2,
          cleaned_text: "blurry scan text",
          language: "en",
          quality_score: 0.42,
          needs_ocr: true,
          ocr_applied: false,
          extraction_status: "needs_review",
          error_message: "Low extraction quality on scanned page 2.",
        },
      ],
      chunks: [
        {
          id: "chk-1",
          chunk_index: 0,
          section_path: "Section 1 > Leave",
          page_start: 1,
          page_end: 2,
          page_range: "pp. 1-2",
          text: "Annual leave is 14 days.",
          token_count: 6,
          language: "en",
          visibility: "all",
          has_injection_flag: false,
        },
      ],
    };

    const previewSpy = vi.spyOn(policiesHook, "usePolicyPreview").mockReturnValue({
      data: needsReviewPreview,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    fixPageMutateAsync.mockResolvedValueOnce(
      makePolicyDoc({
        id: "pol-v3",
        ingestion_status: "indexed",
        lifecycle_status: "draft",
      })
    );
    fixChunkMutateAsync.mockResolvedValueOnce({
      ...needsReviewPreview.chunks[0],
      text: "Annual leave is 20 days.",
      section_path: "Section 1 > Updated Leave",
    });
    publishMutateAsync.mockResolvedValueOnce({
      document: makePolicyDoc({
        id: "pol-v3",
        ingestion_status: "indexed",
        lifecycle_status: "review",
      }),
      approval_id: "apr-99",
      approval_status: "pending",
    });

    const { rerender } = render(
      <>
        <PolicyPreviewPage />
        <ToastProvider />
      </>
    );

    expect(screen.getAllByText(/Needs OCR \/ Manual Fix/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Quality: 42%/i)).toBeInTheDocument();

    const blockedPublishBtn = screen.getByRole("button", {
      name: /Submit for Publish Approval/i,
    });
    expect(blockedPublishBtn).toBeDisabled();

    // Fix Page 2 inline
    fireEvent.click(screen.getByRole("button", { name: /Fix Page 2 Text/i }));
    fireEvent.change(screen.getByLabelText(/Repaired Text for Page 2/i), {
      target: { value: "Employees receive 20 paid annual leave days." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save Fix & Re-index/i }));

    await waitFor(() => {
      expect(fixPageMutateAsync).toHaveBeenCalledWith({
        pageNumber: 2,
        payload: { cleaned_text: "Employees receive 20 paid annual leave days." },
      });
    });

    // Edit Chunk 1 inline
    fireEvent.click(screen.getByRole("button", { name: /Edit Chunk 1/i }));
    fireEvent.change(screen.getByLabelText(/Section Path/i), {
      target: { value: "Section 1 > Updated Leave" },
    });
    fireEvent.change(screen.getByLabelText(/Chunk Text/i), {
      target: { value: "Annual leave is 20 days." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save Chunk/i }));

    await waitFor(() => {
      expect(fixChunkMutateAsync).toHaveBeenCalledWith({
        chunkId: "chk-1",
        payload: {
          text: "Annual leave is 20 days.",
          section_path: "Section 1 > Updated Leave",
          mark_reviewed: false,
        },
      });
    });

    // Now simulate indexed draft state and verify Publish button unlocks
    previewSpy.mockReturnValue({
      data: {
        ...needsReviewPreview,
        document: makePolicyDoc({
          id: "pol-v3",
          title: "Employee Handbook 2026",
          version: 3,
          ingestion_status: "indexed",
          lifecycle_status: "draft",
        }),
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    rerender(
      <>
        <PolicyPreviewPage />
        <ToastProvider />
      </>
    );

    const unlockedPublishBtn = screen.getByRole("button", {
      name: /Submit for Publish Approval/i,
    });
    expect(unlockedPublishBtn).not.toBeDisabled();
    fireEvent.click(unlockedPublishBtn);

    await waitFor(() => {
      expect(publishMutateAsync).toHaveBeenCalledWith({
        policyId: "pol-v3",
        payload: { approver_role: "owner" },
      });
    });
    expect(await screen.findByRole("link", { name: /Open Approvals Inbox/i })).toHaveAttribute(
      "href",
      "/approvals"
    );
  });

  it("renders OCR & Review Queue for HR Head, disables review-queue query for plain Employee, and resolves preview header route", () => {
    expect(resolveRouteTitle("/policies/pol-123/preview").title).toBe("Policy Preview & Fix");
    expect(resolveRouteTitle("/policies").title).toBe("Corporate Policy Brain");

    const reviewItems: PolicyReviewQueueItem[] = [
      {
        document: makePolicyDoc({
          id: "pol-ocr",
          title: "Scanned Factory Notice",
          ingestion_status: "needs_review",
          low_confidence_pages: [{ page_number: 2, quality_score: 0.35 }],
          error_message: "OCR confidence below 70% on page 2",
        }),
        low_confidence_count: 1,
      },
    ];

    const queueSpy = vi.spyOn(policiesHook, "usePolicyReviewQueue").mockReturnValue({
      data: reviewItems,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    const { unmount } = render(<PoliciesPage />);
    expect(queueSpy).toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByRole("tab", { name: /OCR & Review Queue/i }));
    expect(screen.getByText("Scanned Factory Notice")).toBeInTheDocument();
    expect(screen.getByText(/Page 2 \(35% quality\)/i)).toBeInTheDocument();
    expect(screen.getByText(/OCR confidence below 70% on page 2/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Inspect & Fix/i })).toHaveAttribute(
      "href",
      "/policies/pol-ocr/preview"
    );
    unmount();

    // Non-HR plain employee must NOT trigger /review-queue (enabled=false)
    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      data: {
        user_id: "u-emp",
        clerk_user_id: "clerk-emp",
        email: "employee@acme.internal",
        name: "Regular Employee",
        tenant_id: "t-acme",
        clerk_org_id: "org_acme",
        tenant_name: "Acme Tech Solutions",
        role: "employee",
        is_hr_head: false,
        is_payroll_admin: false,
        permissions: [],
      },
      isLoading: false,
      error: null,
    } as any);

    render(<PoliciesPage />);
    expect(queueSpy).toHaveBeenLastCalledWith(false);
    expect(screen.queryByRole("tab", { name: /OCR & Review Queue/i })).not.toBeInTheDocument();
  });

  it("shows StandbyBanner ONLY on genuine offline failures and never on live HTTP errors", () => {
    // Live HTTP 403 -> explicit role="alert", NO StandbyBanner
    vi.spyOn(policiesHook, "usePolicies").mockReturnValue({
      data: [],
      isLoading: false,
      error: new ApiHttpError(403, "FORBIDDEN", "Access denied for this tenant."),
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    const { unmount } = render(<PoliciesPage />);
    expect(screen.queryByText(/FastAPI Backend in Standby Mode/i)).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Access denied for this tenant.");
    unmount();

    // Genuine Offline -> renders StandbyBanner
    vi.spyOn(policiesHook, "usePolicies").mockReturnValue({
      data: [],
      isLoading: false,
      error: new ApiOfflineError("OFFLINE: FastAPI backend is unreachable."),
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    render(<PoliciesPage />);
    expect(screen.getByText(/FastAPI Backend in Standby Mode/i)).toBeInTheDocument();
  });

  it("renders live ingestion status badges and preview links in Onboarding Step 2", async () => {
    vi.spyOn(policiesHook, "usePolicies").mockReturnValue({
      data: [
        makePolicyDoc({
          id: "pol-idx",
          title: "Onboarding Code of Conduct",
          ingestion_status: "indexed",
          lifecycle_status: "draft",
        }),
        makePolicyDoc({
          id: "pol-rev",
          title: "Scanned Addendum",
          ingestion_status: "needs_review",
          lifecycle_status: "draft",
        }),
      ],
      isLoading: false,
      error: null,
    } as any);

    uploadMutateAsync.mockResolvedValueOnce(
      makePolicyDoc({
        id: "pol-step2",
        title: "Safety Policy",
        doc_type: "helpdesk_knowledge",
        visibility: "managers",
        ingestion_status: "indexed",
      })
    );

    render(
      <Step2UploadPolicies isSavingStep={false} onBack={vi.fn()} onSaveAndContinue={vi.fn()} />
    );

    expect(screen.getByText("Indexed")).toBeInTheDocument();
    expect(screen.getByText("Needs Review")).toBeInTheDocument();
    expect(screen.queryByText(/Queued for Stage 5 AI Ingestion/i)).not.toBeInTheDocument();

    const previewLinks = screen.getAllByRole("link", { name: /Preview & Fix/i });
    expect(previewLinks[0]).toHaveAttribute("href", "/policies/pol-idx/preview");

    fireEvent.change(screen.getByLabelText(/Document Type/i), {
      target: { value: "helpdesk_knowledge" },
    });
    fireEvent.change(screen.getByLabelText(/Visibility/i), {
      target: { value: "managers" },
    });
    fireEvent.change(screen.getByLabelText(/Language/i), {
      target: { value: "en" },
    });
    const file = new File(["Safety handbook text"], "safety.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText(/Select Policy File/i), {
      target: { files: [file] },
    });

    await waitFor(() => {
      expect(uploadMutateAsync).toHaveBeenCalledWith({
        file,
        title: undefined,
        docType: "helpdesk_knowledge",
        visibility: "managers",
        language: "en",
      });
    });
  });
});
