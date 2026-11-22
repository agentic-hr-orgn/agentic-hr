import { test, expect } from "@playwright/test";

test.describe("Stage 5 Policy Library, Bilingual Search & Onboarding Step 2 E2E", () => {
  test("policies workspace renders Policy Library, Bilingual Search (dir=auto), and OCR Review Queue without console errors", async ({
    page,
  }) => {
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

    await page.route("**/api/v1/policies**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
    });

    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/policies");

    await expect(page.getByText("Stage 6 ✓ · Stage 7 next").first()).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Corporate Policy Brain \(Bilingual RAG\)/i })
    ).toBeVisible();

    const libraryTab = page.getByRole("tab", { name: /Policy Library/i });
    const searchTab = page.getByRole("tab", { name: /Bilingual Search/i });
    const reviewTab = page.getByRole("tab", { name: /OCR & Review Queue/i });

    await expect(libraryTab).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("heading", { name: /Upload Policy Document/i })).toBeVisible();

    // Switch to Bilingual Search tab & verify dir="auto" searchbox
    await searchTab.click();
    await expect(searchTab).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("searchbox")).toHaveAttribute("dir", "auto");
    await expect(page.getByText(/Retrieval preview — cited Q&A arrives in Stage 7/i)).toBeVisible();

    // Switch to OCR & Review Queue tab
    await reviewTab.click();
    await expect(reviewTab).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText(/OCR & Ingestion Review Queue/i)).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("onboarding Step 2 renders Stage 5 live policy metadata controls cleanly", async ({
    page,
  }) => {
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

    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/onboarding");

    await expect(page.getByRole("heading", { name: /Workspace Onboarding Wizard/i })).toBeVisible();
    await page.getByRole("button", { name: /Upload Policies/i }).click();

    await expect(page.getByText(/Step 2 — Upload Corporate HR Policies/i)).toBeVisible();
    await expect(page.getByLabel(/Document Type/i)).toBeVisible();
    await expect(page.getByLabel(/Visibility/i)).toBeVisible();
    await expect(page.getByLabel(/Language/i)).toBeVisible();

    expect(errors).toEqual([]);
  });
});
