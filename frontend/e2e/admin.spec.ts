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

// Uses another member's execution: authorization and diagnostics come from the API.
test("admin can inspect execution lifecycle and server-projected diagnostics", async ({
  page,
  request,
}) => {
  const admin = await createTestUser();
  const owner = await createTestUser();
  const unrelated = await createTestUser();
  promoteToAdmin(admin.email);
  const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";
  const headers = { Authorization: `Bearer ${owner.accessToken}` };
  const workflow = await request.post(`${apiUrl}/workflows`, {
    headers,
    data: {
      name: "PW Diagnostic Workflow",
      isPublic: true,
      isProduction: true,
      nodes: [
        { id: "s", type: "start", position: { x: 0, y: 0 }, data: { label: "Start" } },
        { id: "e", type: "end", position: { x: 400, y: 0 }, data: { label: "End" } },
      ],
      edges: [{ id: "e1", source: "s", target: "e" }],
    },
  });
  expect(workflow.status()).toBe(201);
  const { id: workflowId } = await workflow.json();
  const execution = await request.post(`${apiUrl}/executions`, {
    headers,
    data: { workflowId, input: {} },
  });
  expect(execution.status()).toBe(202);
  const { id: executionId } = await execution.json();
  await loginAs(page, admin.email, admin.password);
  const diagnosticResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/executions/${executionId}/observability`) &&
      response.status() === 200
  );
  await page.goto(`/runs/${workflowId}/executions/${executionId}`);
  const summary = await (await diagnosticResponse).json();
  expect(summary.adminDiagnostics.threadId).toBeTruthy();
  expect(typeof summary.adminDiagnostics.deliveryAttempts).toBe("number");
  await expect(page.getByRole("heading", { name: "Execution status", exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Administrator diagnostics", exact: true })
  ).toBeVisible();
  await expect(page.getByText(summary.adminDiagnostics.threadId, { exact: true })).toBeVisible();
  await expect(page.getByText("Delivery attempts", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Attempt 1", exact: true })).toBeVisible({
    timeout: 30_000,
  });
  for (const suffix of ["", "/observability", "/attempts?limit=50&offset=0"]) {
    const denied = await request.get(`${apiUrl}/executions/${executionId}${suffix}`, {
      headers: { Authorization: `Bearer ${unrelated.accessToken}` },
    });
    expect(denied.status()).toBe(404);
  }
});
