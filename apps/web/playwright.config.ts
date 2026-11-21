import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL || "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "node node_modules/next/dist/bin/next start -p 3000",
    cwd: __dirname,
    url: "http://localhost:3000/sign-in",
    env: {
      PLAYWRIGHT: "true",
      ALLOW_E2E_AUTH_BYPASS: "true",
      PORT: "3000",
    },
    reuseExistingServer: !process.env.CI,
    timeout: 60 * 1000,
  },
});
