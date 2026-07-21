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
import { createTestUser, cleanupTestUsers } from "./fixtures/test-user";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

test.afterEach(async () => {
  await cleanupTestUsers();
});

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

const DATETIME_FIELD_WORKFLOW_BODY = {
  name: "PW DateTime Field Workflow",
  description: "Created by Playwright date-field spec",
  nodes: [
    {
      id: "s",
      type: "start",
      position: { x: 0, y: 0 },
      data: {
        label: "Start",
        inputVariables: [
          {
            name: "extract_timestamp",
            type: "datetime",
            required: true,
            description: "Extract timestamp",
          },
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

async function createAndPublishWorkflow(
  accessToken: string,
  body: Record<string, unknown> = DATE_FIELD_WORKFLOW_BODY
): Promise<string> {
  const ctx = await playwrightRequest.newContext();
  const res = await ctx.post(`${apiUrl}/workflows`, {
    data: body,
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (res.status() !== 201) {
    throw new Error(`createWorkflow failed: ${res.status()} ${await res.text()}`);
  }
  const responseBody = (await res.json()) as { id: string };
  await ctx.dispose();
  return responseBody.id;
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
  return new Date().toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
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

test("end-user picks a date+time via the datetime picker and it's submitted as a merged ISO string", async ({
  page,
}) => {
  const user = await createTestUser();
  const workflowId = await createAndPublishWorkflow(user.accessToken, DATETIME_FIELD_WORKFLOW_BODY);

  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });

  await page.goto(`/runs/${workflowId}`);
  await expect(page.locator("h2")).toContainText("PW DateTime Field Workflow", {
    timeout: 10_000,
  });

  // Open the picker and pick today's day first (the time input merges onto
  // whatever date is already selected — see DateTimePickerButton).
  await page.locator("#f-extract_timestamp").click();
  await page.getByRole("button", { name: todayLongLabel() }).click();

  // The popover does not auto-close on a day pick, so the time input is
  // still reachable. It's rendered into a Base UI portal (not a DOM
  // sibling of the trigger), so locate it globally — only one popover
  // is open at a time.
  await page.locator('input[type="time"]').fill("14:30");

  // Close the popover (click the trigger again) and confirm the trigger
  // text reflects both the date and the time, per formatDateTimeDisplay.
  await page.locator("#f-extract_timestamp").click();
  await expect(page.locator("#f-extract_timestamp")).toContainText(`${todayShortLabel()} 2:30 PM`);

  await page.getByRole("button", { name: /run|submit/i }).click();
  await expect(page).toHaveURL(/\/runs\/.+\/executions\/.+/, { timeout: 15_000 });

  const executionId = page.url().split("/executions/")[1];
  const ctx = await playwrightRequest.newContext();
  const res = await ctx.get(`${apiUrl}/executions/${executionId}`, {
    headers: { Authorization: `Bearer ${user.accessToken}` },
  });
  const execution = (await res.json()) as { input: Record<string, unknown> };
  expect(execution.input.extract_timestamp).toBe(`${todayISO()}T14:30:00`);
  await ctx.dispose();
});
