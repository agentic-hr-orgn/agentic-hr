import { test, expect } from "@playwright/test";

test.describe("Frontend Smoke & Authentication Tests", () => {
  test("unauthenticated visitors are redirected to sign-in page", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/.*\/sign-in/);
    await expect(
      page.getByRole("heading", { name: /SME Workforce Platform Sign In/i })
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
});
