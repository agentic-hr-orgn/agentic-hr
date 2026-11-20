import { test, expect } from "@playwright/test";

test.describe("Frontend Smoke & Authentication Tests", () => {
  test("unauthenticated visitors are redirected to sign-in page", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/.*\/sign-in/);
    await expect(
      page.getByRole("heading", { name: /Sign in|SME Workforce Platform Sign In/i })
    ).toBeVisible();
  });

  test("sign-up page loads with platform branding and security notice", async ({ page }) => {
    await page.goto("/sign-up");
    await expect(
      page.getByRole("heading", { name: /Create Your Workforce Account/i })
    ).toBeVisible();
  });

  test("authenticated dashboard loads with brand, hero section, and navigation", async ({
    page,
  }) => {
    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/");
    await expect(page).toHaveTitle(/Agentic HR Core/i);
    await expect(page.getByRole("heading", { name: /Agentic HR Core Platform/i })).toBeVisible();
    await expect(page.getByText("USE_FAKE_INTEGRATIONS=true")).toBeVisible();
  });

  test("navigation links navigate to placeholder routes", async ({ page }) => {
    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/");
    await page.click('a[href="/candidates"]');
    await expect(page).toHaveURL(/.*\/candidates/);
    await expect(page.getByRole("heading", { name: /Candidate Pipeline/i })).toBeVisible();
  });

  test("settings page renders Google Workspace connector card, status badge, and controls", async ({
    page,
  }) => {
    page.on("pageerror", (err) => console.log("BROWSER ERROR:", err));
    page.on("console", (msg) => {
      if (msg.type() === "error") console.log("BROWSER CONSOLE ERROR:", msg.text());
    });
    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: /Platform Settings & Connectors/i })
    ).toBeVisible();
    await expect(page.getByText("Google Workspace Connector (ADR 006)")).toBeVisible();
    await expect(page.getByText("agentic.hr.test.ubaid.owner@gmail.com")).toBeVisible();
    await expect(page.getByText("gmail.send")).toBeVisible();
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();

    const switchBtn = page.getByRole("switch", { name: "Toggle Read-Only Mode" });
    await expect(switchBtn).toBeVisible();
    await expect(switchBtn).toHaveAttribute("data-hydrated", "true");
    await expect(switchBtn).toHaveAttribute("aria-checked", "false");

    // 1. Toggle Read-Only Mode ON
    await switchBtn.click();
    await expect(switchBtn).toHaveAttribute("aria-checked", "true");
    await expect(page.getByText("Read-Only Active", { exact: false })).toBeVisible();
    await expect(page.getByText("Read-Only Mode is active", { exact: false })).toBeVisible();

    // 2. Toggle Read-Only Mode OFF
    await switchBtn.click();
    await expect(switchBtn).toHaveAttribute("aria-checked", "false");
    await expect(page.getByText("Write operations enabled", { exact: false })).toBeVisible();

    // 3. Click Refresh Token
    const refreshBtn = page.getByRole("button", { name: /Refresh Token/i });
    await refreshBtn.click();
    await expect(
      page.getByText("OAuth access token refreshed successfully (+1h expiry).")
    ).toBeVisible();

    // 4. Click Disconnect with confirm dialog OK
    page.once("dialog", async (dialog) => {
      await dialog.accept();
    });
    const disconnectBtn = page.getByRole("button", { name: /Disconnect/i });
    await disconnectBtn.click();
    await expect(page.getByText("Revoked", { exact: true })).toBeVisible();

    // 5. Click Reconnect OAuth to restore Connected status
    const reconnectBtn = page.getByRole("button", { name: /Reconnect OAuth/i }).first();
    await reconnectBtn.click();
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  });

  test("directory route permanently redirects to /employees and core pages load without console or hydration errors", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        // Ignore expected external/offline network errors when running standalone Playwright smoke
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

    // Verify /directory -> /employees redirect
    await page.goto("/directory");
    await expect(page).toHaveURL(/.*\/employees/);
    await expect(
      page.getByRole("heading", { name: /Employee Directory & Sheets Synchronization/i })
    ).toBeVisible();

    // Verify /approvals loads cleanly with proper header title
    await page.goto("/approvals");
    await expect(page.getByRole("heading", { name: "Approvals Inbox" }).first()).toBeVisible();

    expect(errors).toEqual([]);
  });
});
