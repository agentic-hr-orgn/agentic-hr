import { test, expect, type Page } from "@playwright/test";

function attachErrorCollector(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      const text = msg.text();
      if (
        !text.includes("ERR_CONNECTION_REFUSED") &&
        !text.includes("Failed to load resource") &&
        !text.includes("clerk.accounts.dev")
      ) {
        errors.push(`console.error: ${text}`);
      }
    }
  });
  return errors;
}

async function mockBaseSessionApis(page: Page): Promise<void> {
  await page.route("**/health", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "healthy",
        database: { connected: true, pgvector_enabled: true },
        use_fake_integrations: true,
      }),
    });
  });

  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "e2e-owner-01",
        clerk_user_id: "user_e2e_owner",
        email: "owner@acme.internal",
        name: "Acme Owner",
        tenant_id: "11111111-1111-1111-1111-111111111111",
        clerk_org_id: "org_acme_tech_01",
        tenant_name: "Acme Tech Solutions",
        role: "owner",
        is_hr_head: true,
        is_payroll_admin: true,
        permissions: ["owner"],
      }),
    });
  });

  await page.route("**/api/v1/notifications**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], unread_count: 0 }),
    });
  });
}

test.describe("Stage 7 Policy Brain Chat, Policy Gaps & Helpdesk E2E", () => {
  test("/chat streams answer with clickable citation deep-linking to /policies/[id]/preview, feedback & Ask HR with 0 errors", async ({
    page,
  }) => {
    const errors = attachErrorCollector(page);
    await mockBaseSessionApis(page);

    let streamedCompleted = false;

    await page.route("**/api/v1/chat/sessions", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "sess-e2e-1",
            tenant_id: "11111111-1111-1111-1111-111111111111",
            user_id: "e2e-owner-01",
            title: "Annual Leave Policy",
            language: "en",
            last_message_at: "2026-11-23T15:00:00Z",
            created_at: "2026-11-23T14:55:00Z",
            updated_at: "2026-11-23T15:00:00Z",
          },
        ]),
      });
    });

    await page.route("**/api/v1/chat/sessions/sess-e2e-1/messages", async (route) => {
      const payload = streamedCompleted
        ? [
            {
              id: "msg-u-1",
              tenant_id: "11111111-1111-1111-1111-111111111111",
              session_id: "sess-e2e-1",
              role: "user",
              content: "How many annual leave days do full-time staff receive?",
              language: "en",
              citations: [],
              tool_calls: [],
              abstained: false,
              created_at: "2026-11-23T15:01:00Z",
            },
            {
              id: "msg-e2e-1",
              tenant_id: "11111111-1111-1111-1111-111111111111",
              session_id: "sess-e2e-1",
              role: "assistant",
              content: "Employees receive 20 paid annual leave days.",
              language: "en",
              intent: "policy_qa",
              citations: [
                {
                  chunk_id: "chk-leave-1",
                  document_id: "doc-leave-1",
                  title: "Employee Leave Policy",
                  version: 2,
                  section: "Section 1.2",
                  page: 2,
                  snippet: "Full-time employees receive 20 paid annual leave days per year.",
                },
              ],
              tool_calls: [],
              abstained: false,
              ticket_id: null,
              ticket_number: null,
              handoff_payload: null,
              topic_tag: "leave_policy",
              latency_ms: 42,
              model_tier: "strong",
              created_at: "2026-11-23T15:01:02Z",
            },
          ]
        : [];

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(payload),
      });
    });

    await page.route("**/api/v1/chat/sessions/sess-e2e-1/stream", async (route) => {
      streamedCompleted = true;
      const sseBody = [
        `event: status\ndata: ${JSON.stringify({ node: "retrieve" })}\n\n`,
        `event: token\ndata: ${JSON.stringify({ text: "Employees receive 20 paid annual leave days." })}\n\n`,
        `event: done\ndata: ${JSON.stringify({
          message_id: "msg-e2e-1",
          session_id: "sess-e2e-1",
          answer: "Employees receive 20 paid annual leave days.",
          language: "en",
          intent: "policy_qa",
          citations: [
            {
              chunk_id: "chk-leave-1",
              document_id: "doc-leave-1",
              title: "Employee Leave Policy",
              version: 2,
              section: "Section 1.2",
              page: 2,
              snippet: "Full-time employees receive 20 paid annual leave days per year.",
            },
          ],
          tool_calls: [],
          abstained: false,
          ticket_id: null,
          ticket_number: null,
          handoff_payload: null,
          topic_tag: "leave_policy",
          latency_ms: 42,
        })}\n\n`,
      ].join("");

      await route.fulfill({
        status: 200,
        contentType: "text/event-stream; charset=utf-8",
        headers: { "Cache-Control": "no-cache" },
        body: sseBody,
      });
    });

    await page.route("**/api/v1/chat/messages/msg-e2e-1/feedback", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "fb-e2e-1",
          tenant_id: "11111111-1111-1111-1111-111111111111",
          message_id: "msg-e2e-1",
          user_id: "e2e-owner-01",
          rating: "up",
          created_at: "2026-11-23T15:01:10Z",
        }),
      });
    });

    await page.route("**/api/v1/chat/messages/msg-e2e-1/ask-hr", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ticket_id: "tkt-e2e-5",
          ticket_number: "TKT-2026-0005",
          category: "hr_policy",
          priority: "medium",
          message_id: "msg-e2e-1",
        }),
      });
    });

    await page.route("**/api/v1/policies/doc-leave-1/preview", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          document: {
            id: "doc-leave-1",
            tenant_id: "11111111-1111-1111-1111-111111111111",
            title: "Employee Leave Policy",
            doc_type: "company_policy",
            visibility: "all",
            version: 2,
            file_name: "leave_policy.pdf",
            mime_type: "application/pdf",
            size_bytes: 2048,
            storage_provider: "local",
            storage_key: "t/policies/leave.pdf",
            ingestion_status: "indexed",
            lifecycle_status: "active",
            display_status: "ACTIVE",
            page_count: 2,
            progress_pct: 100,
            low_confidence_pages: [],
            has_prompt_injection_warning: false,
            created_at: "2026-11-20T09:00:00Z",
            updated_at: "2026-11-20T09:05:00Z",
          },
          pages: [
            {
              id: "pg-1",
              page_number: 1,
              raw_text: "Overview",
              cleaned_text: "Overview page",
              language: "en",
              extraction_status: "ok",
              quality_score: 0.99,
              needs_ocr: false,
              ocr_applied: false,
            },
            {
              id: "pg-2",
              page_number: 2,
              raw_text: "20 paid annual leave days",
              cleaned_text: "Full-time employees receive 20 paid annual leave days per year.",
              language: "en",
              extraction_status: "ok",
              quality_score: 0.99,
              needs_ocr: false,
              ocr_applied: false,
            },
          ],
          chunks: [
            {
              id: "chk-leave-1",
              chunk_index: 0,
              section_path: "Section 1.2",
              page_range: "2",
              language: "en",
              visibility: "all",
              text: "Full-time employees receive 20 paid annual leave days per year.",
              token_count: 14,
              has_injection_flag: false,
            },
          ],
        }),
      });
    });

    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/chat");

    await expect(page.getByText("Stage 7 ✓ · Stage 8 next").first()).toBeVisible();
    await expect(
      page.locator("main").getByRole("heading", { level: 1, name: "Policy Brain Chat" })
    ).toBeVisible();

    const composer = page.getByLabel("Ask Policy Brain a question");
    await expect(composer).toHaveAttribute("dir", "auto");
    await composer.fill("How many annual leave days do full-time staff receive?");
    await page.getByRole("button", { name: /^Send$/i }).click();

    await expect(page.getByText("Employees receive 20 paid annual leave days.")).toBeVisible();

    // Feedback + Ask HR
    await page.getByRole("button", { name: "Thumbs Up" }).click();
    await page.getByRole("button", { name: "Ask HR" }).click();
    await expect(page.getByText(/HR Ticket Created \(TKT-2026-0005\)/i)).toBeVisible();

    // Click verified citation chip -> deep-link /policies/doc-leave-1/preview?page=2&chunk=chk-leave-1
    await page.getByRole("link", { name: /Employee Leave Policy/i }).click();
    await expect(page).toHaveURL(/\/policies\/doc-leave-1\/preview\?page=2&chunk=chk-leave-1/);
    await expect(page.getByText("Cited Policy Source Highlighted")).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("/policy-gaps renders KPI cards & grouped topic accordion for Owner/HR Head with 0 errors", async ({
    page,
  }) => {
    const errors = attachErrorCollector(page);
    await mockBaseSessionApis(page);

    await page.route("**/api/v1/chat/policy-gaps", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_gaps: 3,
          groups: [
            {
              topic_tag: "parental_leave",
              total_count: 3,
              abstained_count: 2,
              downvoted_count: 1,
              sample_questions: ["Is adoption leave covered under company policy?"],
              ticket_numbers: ["TKT-2026-0031"],
              latest_at: "2026-11-23T12:00:00Z",
            },
          ],
        }),
      });
    });

    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/policy-gaps");

    await expect(page.getByText("Stage 7 ✓ · Stage 8 next").first()).toBeVisible();
    await expect(
      page.locator("main").getByRole("heading", { level: 1, name: "HR Policy Gaps" })
    ).toBeVisible();
    await expect(page.getByText("Total Policy Gaps")).toBeVisible();
    await expect(page.getByText("Is adoption leave covered under company policy?")).toBeVisible();
    await expect(page.getByRole("link", { name: /TKT-2026-0031/i })).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("/helpdesk renders Ask KB card, My Tickets, Staff Queue, Assignment Rules & Ticket Detail with 0 errors", async ({
    page,
  }) => {
    const errors = attachErrorCollector(page);
    await mockBaseSessionApis(page);

    const sampleTicket = {
      id: "tkt-e2e-1",
      tenant_id: "11111111-1111-1111-1111-111111111111",
      ticket_number: "TKT-2026-0001",
      requester_user_id: "e2e-owner-01",
      assigned_to_user_id: "e2e-owner-01",
      category: "hr_policy",
      priority: "medium",
      status: "resolved",
      subject: "Clarify annual leave encashment",
      description: "Can unused annual leave be encashed at year end?",
      source: "manual",
      source_session_id: null,
      sla_due_at: "2099-11-24T17:00:00Z",
      sla_reminder_at: "2099-11-24T13:00:00Z",
      sla_reminder_sent_at: null,
      sla_escalated_at: null,
      resolved_at: "2026-11-23T11:00:00Z",
      closed_at: null,
      reopen_until: "2099-12-01T00:00:00Z",
      satisfaction_rating: null,
      satisfaction_comment: null,
      created_at: "2026-11-23T09:00:00Z",
      updated_at: "2026-11-23T11:00:00Z",
    };

    await page.route("**/api/v1/tickets/me**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([sampleTicket]),
      });
    });

    await page.route("**/api/v1/tickets/queue**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([sampleTicket]),
      });
    });

    await page.route("**/api/v1/tickets/rules", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "rule-hr",
            tenant_id: "11111111-1111-1111-1111-111111111111",
            category: "hr_policy",
            default_assignee_user_id: "e2e-owner-01",
            default_assignee_role: "hr_head",
            sla_hours: 24,
            reminder_before_hours: 4,
            reopen_window_days: 7,
            created_at: "2026-11-20T09:00:00Z",
            updated_at: "2026-11-20T09:00:00Z",
          },
        ]),
      });
    });

    await page.route("**/api/v1/tickets/tkt-e2e-1", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...sampleTicket,
          messages: [
            {
              id: "tm-int-1",
              tenant_id: "11111111-1111-1111-1111-111111111111",
              ticket_id: "tkt-e2e-1",
              author_user_id: "e2e-owner-01",
              body: "Internal HR audit check complete.",
              is_internal: true,
              created_at: "2026-11-23T10:30:00Z",
            },
          ],
        }),
      });
    });

    await page.route("**/api/v1/employees**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "emp-e2e-1",
              tenant_id: "11111111-1111-1111-1111-111111111111",
              user_id: "e2e-owner-01",
              employee_code: "ACM-001",
              full_name: "Acme Owner",
              email: "owner@acme.internal",
              job_title: "Owner",
              employment_type: "full_time",
              join_date: "2024-01-01",
              status: "active",
              created_at: "2026-11-20T09:00:00Z",
              updated_at: "2026-11-20T09:00:00Z",
            },
          ],
          total: 1,
          page: 1,
          page_size: 50,
          total_pages: 1,
        }),
      });
    });

    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/helpdesk");

    await expect(page.getByText("Stage 7 ✓ · Stage 8 next").first()).toBeVisible();
    await expect(
      page.locator("main").getByRole("heading", { level: 1, name: /HR & IT Helpdesk/i })
    ).toBeVisible();
    await expect(page.getByText("Ask Helpdesk Knowledge Base")).toBeVisible();
    await expect(page.getByText("Internal Only")).toBeVisible();
    await expect(page.getByLabel(/Internal Note \(Staff Only\)/i)).toBeVisible();
    await expect(page.getByText(/Rate Support Experience/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /Reopen Ticket/i })).toBeVisible();

    await page.getByRole("tab", { name: /^Staff Queue/i }).click();
    await expect(page.getByText(/Helpdesk Staff Queue/i)).toBeVisible();

    await page.getByRole("tab", { name: /^Assignment Rules/i }).click();
    await expect(page.getByText(/Category Routing & SLA Rules/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "Save HR Policy rule" })).toBeVisible();

    expect(errors).toEqual([]);
  });
});
