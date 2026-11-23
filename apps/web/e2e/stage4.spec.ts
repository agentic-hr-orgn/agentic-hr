import { test, expect } from "@playwright/test";

test.describe("Stage 4 (Milestone B) E2E Smoke & Navigation Tests", () => {
  test("dashboard displays Stage 4 badge and navigates to Onboarding Wizard", async ({ page }) => {
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
    await page.goto("/");

    await expect(page.getByText("Stage 7 ✓ · Stage 8 next").first()).toBeVisible();
    await page.click('a[href="/onboarding"]');
    await expect(page).toHaveURL(/.*\/onboarding/);
    await expect(page.getByRole("heading", { name: /Workspace Onboarding Wizard/i })).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("resumable 5-step onboarding wizard renders stepper and Step 1 Connect Tools cleanly", async ({
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
    await expect(
      page.getByRole("progressbar", { name: /Onboarding setup progress/i })
    ).toBeVisible();
    await expect(page.getByText("Connect Tools").first()).toBeVisible();
    await expect(page.getByText("Upload Policies").first()).toBeVisible();
    await expect(page.getByText("Company Structure").first()).toBeVisible();
    await expect(page.getByText("Add Employees").first()).toBeVisible();
    await expect(page.getByText("Test & Launch").first()).toBeVisible();
    await expect(page.getByText("agentic.hr.test.ubaid.owner@gmail.com")).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("employees directory switches between Directory Table and Org Chart tabs and /directory redirects", async ({
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
    await page.goto("/directory");
    await expect(page).toHaveURL(/.*\/employees/);

    await expect(
      page.getByRole("heading", { name: /Employee Directory & Sheets Synchronization/i })
    ).toBeVisible();

    const dirTab = page.getByRole("tab", { name: /Directory Table/i });
    const orgTab = page.getByRole("tab", { name: /Org Chart/i });
    await expect(dirTab).toHaveAttribute("aria-selected", "true");
    await orgTab.click();
    await expect(orgTab).toHaveAttribute("aria-selected", "true");
    await dirTab.click();
    await expect(dirTab).toHaveAttribute("aria-selected", "true");

    expect(errors).toEqual([]);
  });

  test("global header notification bell renders without overlap and toggles popover cleanly", async ({
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
    await page.goto("/employees");

    const bellBtn = page.getByRole("button", { name: /Notifications/i });
    await expect(bellBtn).toBeVisible();
    const bellBox = await bellBtn.boundingBox();
    expect(bellBox).not.toBeNull();
    expect(bellBox!.width).toBeGreaterThan(20);
    expect(bellBox!.height).toBeGreaterThan(20);

    await bellBtn.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "Notifications" })).toBeVisible();

    await page.getByRole("button", { name: "Close notifications panel" }).click();
    await expect(dialog).toBeHidden();

    expect(errors).toEqual([]);
  });
});
