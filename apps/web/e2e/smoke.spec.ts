import { test, expect } from "@playwright/test";

test.describe("Frontend Smoke Tests", () => {
  test("dashboard loads with brand, hero section, and navigation", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/Agentic HR Core/i);
    await expect(page.getByRole("heading", { name: /Agentic HR Core Platform/i })).toBeVisible();
    await expect(page.getByText("Stage 1 — Platform Skeleton")).toBeVisible();
    await expect(page.getByText("USE_FAKE_INTEGRATIONS=true")).toBeVisible();
  });

  test("navigation links navigate to placeholder routes", async ({ page }) => {
    await page.goto("/");
    await page.click('a[href="/candidates"]');
    await expect(page).toHaveURL(/.*\/candidates/);
    await expect(page.getByRole("heading", { name: /Candidate Pipeline/i })).toBeVisible();
  });
});
