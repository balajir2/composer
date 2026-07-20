/**
 * date-field.spec.ts — Start-node "date" field type: calendar picker
 * end-to-end.
 *
 * Publishes a workflow whose Start node declares a `report_date` field
 * of type "date", drives the calendar picker in the run form, and
 * confirms the started execution's submitted input carries the plain
 * ISO date string the picker produced.
 *
 * Requires: uvicorn + next dev running (or CI webServer config).
 */

import { test, expect, request as playwrightRequest } from "@playwright/test";
import { createTestUser } from "./fixtures/test-user";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

const DATE_FIELD_WORKFLOW_BODY = {
  name: "PW Date Field Workflow",
  description: "Created by Playwright date-field spec",
  nodes: [
    {
      id: "s",
      type: "start",
      position: { x: 0, y: 0 },
      data: {
        label: "Start",
        inputVariables: [
          { name: "report_date", type: "date", required: true, description: "Report date" },
        ],
      },
    },
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
    data: DATE_FIELD_WORKFLOW_BODY,
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (res.status() !== 201) {
    throw new Error(`createWorkflow failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { id: string };
  await ctx.dispose();
  return body.id;
}

function todayISO(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function todayLongLabel(): string {
  return new Date().toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

function todayShortLabel(): string {
  return new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

test("end-user picks a date via the calendar and it's submitted as plain ISO text", async ({
  page,
}) => {
  const user = await createTestUser();
  const workflowId = await createAndPublishWorkflow(user.accessToken);

  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });

  await page.goto(`/runs/${workflowId}`);
  await expect(page.locator("h2")).toContainText("PW Date Field Workflow", { timeout: 10_000 });

  // Open the calendar and pick today.
  await page.locator("#f-report_date").click();
  await page.getByRole("button", { name: todayLongLabel() }).click();

  // The trigger now shows the picked date.
  await expect(page.locator("#f-report_date")).toContainText(todayShortLabel());

  await page.getByRole("button", { name: /run|submit/i }).click();
  await expect(page).toHaveURL(/\/runs\/.+\/executions\/.+/, { timeout: 15_000 });

  const executionId = page.url().split("/executions/")[1];
  const ctx = await playwrightRequest.newContext();
  const res = await ctx.get(`${apiUrl}/executions/${executionId}`, {
    headers: { Authorization: `Bearer ${user.accessToken}` },
  });
  const execution = (await res.json()) as { input: Record<string, unknown> };
  expect(execution.input.report_date).toBe(todayISO());
  await ctx.dispose();
});
