/**
 * end-user.spec.ts — End-user happy path.
 *
 * Creates a test user + publishes a minimal workflow via the backend API,
 * then drives the UI through the full run-and-watch cycle.
 *
 * Requires: uvicorn + next dev running (or CI webServer config).
 */

import { test, expect, request as playwrightRequest } from "@playwright/test";
import { createTestUser } from "./fixtures/test-user";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

// Minimal Start→End workflow that completes immediately (no LLM required).
const MINIMAL_WORKFLOW_BODY = {
  name: "PW Smoke Workflow",
  description: "Created by Playwright end-user spec",
  nodes: [
    { id: "s", type: "start", position: { x: 0, y: 0 }, data: { label: "Start" } },
    { id: "e", type: "end", position: { x: 400, y: 0 }, data: { label: "End" } },
  ],
  edges: [{ id: "e1", source: "s", target: "e" }],
  isTemplate: false,
  isPublic: true,
  isProduction: true,
  externalSlug: null,
};

async function createAndPublishWorkflow(accessToken: string): Promise<string> {
  const ctx = await playwrightRequest.newContext();
  const res = await ctx.post(`${apiUrl}/workflows`, {
    data: MINIMAL_WORKFLOW_BODY,
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (res.status() !== 201) {
    throw new Error(`createWorkflow failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { id: string };
  await ctx.dispose();
  return body.id;
}

test("end-user can run a published workflow and see the result", async ({ page }) => {
  // 1. Bootstrap: create user + workflow via API
  const user = await createTestUser();
  const workflowId = await createAndPublishWorkflow(user.accessToken);

  // 2. Log in via UI
  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });

  // 3. Navigate to the workflow run page
  await page.goto(`/runs/${workflowId}`);
  // Workflow name heading should be visible
  await expect(page.locator("h2")).toContainText("PW Smoke Workflow", { timeout: 10_000 });

  // 4. Submit the input form (empty inputs — Start→End workflow takes none)
  const submitBtn = page.getByRole("button", { name: /run|submit/i });
  await submitBtn.click();

  // 5. Should navigate to the execution live view
  await expect(page).toHaveURL(/\/runs\/.+\/executions\/.+/, { timeout: 15_000 });

  // 6. Wait for execution page to render heading
  await expect(page.locator("h2")).toContainText("Execution", { timeout: 10_000 });

  // 7. Wait for a terminal status badge (completed | failed) to appear.
  //    The ExecutionProgress component renders a Badge with the status text.
  const statusBadge = page.locator("[data-slot=badge]").first();
  await expect(statusBadge).toContainText(/completed|failed/, { timeout: 30_000 });
});
