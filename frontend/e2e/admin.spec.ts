/**
 * admin.spec.ts — Admin panel smoke tests.
 *
 * Tests:
 *   1. Admin can view /admin/users and promote a member to admin.
 *   2. Admin can share/unshare an MCP server on /admin/mcp-servers.
 *   3. Admin can toggle a built-in tool on /admin/tools.
 *
 * Setup: registers two users, promotes the first to admin via
 * scripts/promote_admin.py (shells out to Python).
 *
 * Requires: uvicorn + next dev running (or CI webServer config).
 */

import { test, expect } from "@playwright/test";
import { execSync } from "child_process";
import path from "path";
import { createTestUser, cleanupTestUsers } from "./fixtures/test-user";

test.afterEach(async () => {
  await cleanupTestUsers();
});

// ---------------------------------------------------------------------------
// Helper: promote a user to admin via the Python script.
// ---------------------------------------------------------------------------

function promoteToAdmin(email: string): void {
  const scriptPath = path.join(__dirname, "..", "scripts", "promote_admin.py");
  const pythonBin =
    process.platform === "win32"
      ? "d:/GitHub/composer/.venv/Scripts/python.exe"
      : "d:/GitHub/composer/.venv/bin/python";
  execSync(`"${pythonBin}" "${scriptPath}" "${email}"`, { stdio: "inherit" });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function loginAs(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("admin can view /admin/users and see members listed", async ({ page }) => {
  const admin = await createTestUser();
  const member = await createTestUser();

  promoteToAdmin(admin.email);

  await loginAs(page, admin.email, admin.password);
  await page.goto("/admin/users");

  await expect(page.locator("h2")).toContainText("Users", { timeout: 10_000 });
  // Both users should appear in the table
  await expect(page.locator(`text=${admin.email}`)).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(`text=${member.email}`)).toBeVisible({ timeout: 10_000 });
});

test("admin can promote a member to admin and back to member", async ({ page }) => {
  const admin = await createTestUser();
  const member = await createTestUser();

  promoteToAdmin(admin.email);

  await loginAs(page, admin.email, admin.password);
  await page.goto("/admin/users");

  await expect(page.locator("h2")).toContainText("Users", { timeout: 10_000 });

  // Locate the member's row. The UserRoleToggle renders a button to promote/demote.
  const memberRow = page.locator("tr", { hasText: member.email });
  await expect(memberRow).toBeVisible({ timeout: 10_000 });

  // Promote: click the toggle button in the member's row
  const promoteBtn = memberRow.getByRole("button", { name: /promote|make admin/i });
  await promoteBtn.click();

  // Badge in the member's row should now show "admin"
  await expect(memberRow.locator("[data-slot=badge]")).toContainText("admin", { timeout: 10_000 });

  // Demote back to member
  const demoteBtn = memberRow.getByRole("button", { name: /demote|make member/i });
  await demoteBtn.click();

  await expect(memberRow.locator("[data-slot=badge]")).toContainText("member", { timeout: 10_000 });
});

test("admin can toggle a built-in tool on /admin/tools", async ({ page }) => {
  const admin = await createTestUser();
  promoteToAdmin(admin.email);

  await loginAs(page, admin.email, admin.password);
  await page.goto("/admin/tools");

  await expect(page.locator("h2")).toContainText("Tools", { timeout: 10_000 });

  // Find the first toggle button on the page and click it to flip state
  const firstToggle = page
    .getByRole("button")
    .filter({ hasText: /enable|disable/i })
    .first();
  await expect(firstToggle).toBeVisible({ timeout: 10_000 });

  const initialText = await firstToggle.innerText();
  await firstToggle.click();

  // Badge / button text should have changed after toggle
  const newText = await firstToggle.innerText();
  expect(newText).not.toBe(initialText);
});
