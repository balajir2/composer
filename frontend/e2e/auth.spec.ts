/**
 * auth.spec.ts — Login / logout flows.
 *
 * Tests:
 *   1. Login with valid credentials lands on /runs.
 *   2. Login with wrong password shows error toast.
 *   3. Logout returns to /login.
 *
 * Requires: uvicorn + next dev running (or CI webServer config).
 */

import { test, expect } from "@playwright/test";
import { createTestUser, cleanupTestUsers } from "./fixtures/test-user";

test.afterEach(async () => {
  await cleanupTestUsers();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function fillAndSubmitLogin(
  page: import("@playwright/test").Page,
  email: string,
  password: string
) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("login with valid credentials lands on /runs", async ({ page }) => {
  const user = await createTestUser();

  await fillAndSubmitLogin(page, user.email, user.password);

  // After successful login the app redirects to / which re-directs to /runs
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });
  // Page heading confirms it loaded
  await expect(page.locator("h2")).toContainText(["Run a workflow", "No workflows to run yet"], {
    timeout: 10_000,
  });
});

test("login with wrong password shows error toast", async ({ page }) => {
  const user = await createTestUser();

  await fillAndSubmitLogin(page, user.email, "wrong-password-xyz");

  // Toast with error message should appear (sonner renders [data-sonner-toast])
  await expect(page.locator("[data-sonner-toast]")).toBeVisible({ timeout: 10_000 });
  // Still on login page
  await expect(page).toHaveURL(/\/login/);
});

test("logout returns to /login", async ({ page }) => {
  const user = await createTestUser();

  // Log in
  await fillAndSubmitLogin(page, user.email, user.password);
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });

  // Find and click Sign out (the app shell renders a sign-out button)
  const signOutBtn = page.getByRole("button", { name: /sign out/i });
  await signOutBtn.click();

  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });
});
