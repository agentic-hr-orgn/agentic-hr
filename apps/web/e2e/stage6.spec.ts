import { test, expect } from "@playwright/test";

test.describe("Stage 6 Leave Management & Attendance E2E", () => {
  test("leave workspace renders Balance Cards, AI Intake vs Plain Form, Inbox, Calendar & HR Overview without console errors", async ({
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

    await page.route("**/api/v1/leave/balances/me**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "bal-annual",
            tenant_id: "11111111-1111-1111-1111-111111111111",
            employee_id: "emp-01",
            employee_name: "Acme Owner",
            leave_type_id: "lt-annual",
            leave_type_code: "annual",
            leave_type_name: "Annual Leave",
            year: 2026,
            allocated_days: "20.00",
            carried_forward_days: "0.00",
            reserved_days: "0.00",
            used_days: "2.00",
            available_days: "18.00",
            is_paid: true,
            accrual_mode: "upfront",
          },
        ]),
      });
    });

    await page.route("**/api/v1/leave/config/types**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "lt-annual",
            tenant_id: "11111111-1111-1111-1111-111111111111",
            code: "annual",
            name: "Annual Leave",
            annual_allowance_days: "20.00",
            accrual_mode: "upfront",
            carry_forward_cap_days: "5.00",
            max_consecutive_days: 15,
            min_notice_days: 3,
            allow_during_probation: false,
            requires_document_after_days: null,
            allow_half_day: true,
            is_paid: true,
            is_active: true,
            created_at: "2026-11-20T09:00:00Z",
            updated_at: "2026-11-20T09:00:00Z",
          },
        ]),
      });
    });

    await page.route("**/api/v1/leave/config/work-week**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "ww-1",
          tenant_id: "11111111-1111-1111-1111-111111111111",
          weekend_days: ["Saturday", "Sunday"],
          probation_months: 3,
          max_team_absent_pct: "30.00",
          min_team_present: 1,
          backup_approver_user_id: null,
          check_in_enabled: true,
          timezone: "Asia/Karachi",
          created_at: "2026-11-20T09:00:00Z",
          updated_at: "2026-11-20T09:00:00Z",
        }),
      });
    });

    await page.route("**/api/v1/leave/config/holidays**", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.route("**/api/v1/leave/requests/me**", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.route("**/api/v1/leave/inbox**", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.route("**/api/v1/leave/calendar**", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.route("**/api/v1/leave/overview**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_requests: 0,
          pending_count: 0,
          pending_hr_exception_count: 0,
          approved_count: 0,
          rejected_count: 0,
          cancelled_count: 0,
          expired_count: 0,
          requests: [],
        }),
      });
    });

    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/leave");

    await expect(page.getByText("Stage 7 ✓ · Stage 8 next").first()).toBeVisible();
    await expect(page.getByText("Annual Leave")).toBeVisible();
    await expect(page.getByText("18")).toBeVisible();

    // Switch between AI Assistant & Plain Form
    await expect(page.getByLabel(/Leave Request Message/i)).toHaveAttribute("dir", "auto");
    await page.getByRole("button", { name: /Plain Leave Form/i }).click();
    await expect(
      page.getByRole("heading", { name: /Structured Leave Application/i })
    ).toBeVisible();

    // Open Manager Inbox, Team Calendar & HR Overview
    await page.getByRole("tab", { name: /Manager Inbox/i }).click();
    await expect(page.getByText(/Manager Approval Inbox/i)).toBeVisible();

    await page.getByRole("tab", { name: /Team Calendar/i }).click();
    await expect(page.getByRole("heading", { name: /Team Leave Calendar/i })).toBeVisible();

    await page.getByRole("tab", { name: /HR Overview/i }).click();
    await expect(page.getByText(/Organization Leave Utilization/i)).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("attendance workspace renders Check-In card, Manager Daily Grid, Excel Import & Month Review without console errors", async ({
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

    await page.route("**/api/v1/departments**", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });

    await page.route("**/api/v1/attendance/me/today**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          today_date: "2026-11-22",
          year_month: "2026-11",
          check_in_enabled: true,
          is_locked: false,
          record: null,
        }),
      });
    });

    await page.route("**/api/v1/attendance/daily**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          target_date: "2026-11-22",
          year_month: "2026-11",
          is_locked: false,
          total_employees: 1,
          marked_count: 0,
          unmarked_count: 1,
          rows: [
            {
              employee_id: "emp-1",
              employee_code: "EMP-001",
              employee_name: "Zainab Ali",
              employee_email: "zainab@acme.internal",
              department_name: "Engineering",
              job_title: "Senior Engineer",
              attendance_id: null,
              attendance_date: "2026-11-22",
              status: null,
              source: null,
              check_in_at: null,
              check_out_at: null,
              notes: null,
              locked: false,
            },
          ],
        }),
      });
    });

    await page.route("**/api/v1/attendance/months/*/review**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          year_month: "2026-11",
          month_start: "2026-11-01",
          month_end: "2026-11-30",
          days_in_month: 30,
          active_employees: 1,
          total_gaps: 0,
          auto_fillable_gaps: 0,
          is_locked: false,
          can_lock: true,
          locked_at: null,
          locked_by: null,
          employees: [
            {
              employee_id: "emp-1",
              employee_code: "EMP-001",
              employee_name: "Zainab Ali",
              employee_email: "zainab@acme.internal",
              department_name: "Engineering",
              present_days: 22,
              wfh_days: 0,
              half_days: 0,
              on_leave_days: 0,
              absent_days: 0,
              holiday_days: 0,
              weekend_days: 8,
              recorded_days: 30,
              missing_days: [],
              missing_count: 0,
            },
          ],
        }),
      });
    });

    await page.setExtraHTTPHeaders({ "x-e2e-test": "true" });
    await page.goto("/attendance");

    await expect(page.getByText("Stage 7 ✓ · Stage 8 next").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /Check In/i })).toBeVisible();
    await expect(page.getByText(/Manager Daily Attendance Grid/i)).toBeVisible();

    await page.getByRole("tab", { name: /Excel Import/i }).click();
    await expect(page.getByText(/Two-Phase Excel Attendance Import/i)).toBeVisible();

    await page.getByRole("tab", { name: /Month Review & Lock/i }).click();
    await expect(page.getByText(/Month Completeness Review & Lock/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /^Lock Month$/i })).toBeEnabled();

    expect(errors).toEqual([]);
  });
});
