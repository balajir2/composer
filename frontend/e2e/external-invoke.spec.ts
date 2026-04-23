/**
 * external-invoke.spec.ts — API-only: create API key → POST /api/run/{slug}.
 *
 * This spec is entirely API-driven (no browser UI).  It uses Playwright's
 * `request` fixture, which is a thin HTTP client — no browser launch needed.
 *
 * Flow:
 *   1. Register a user via POST /auth/register.
 *   2. Create an API key via POST /api-keys.
 *   3. Create and publish a workflow via POST /workflows.
 *   4. POST /api/run/{slug} with the API key (Bearer ck_*).
 *   5. Assert response 200/202 and { executionId } in body.
 *   6. Poll GET /executions/{id} until terminal; assert final status.
 *
 * Requires: uvicorn running (next dev optional for this spec).
 */

import { test, expect } from "@playwright/test";
import crypto from "crypto";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

const MINIMAL_PUBLISHED_WORKFLOW = {
  name: "PW External Invoke",
  description: "Created by Playwright external-invoke spec",
  nodes: [
    { id: "s", type: "start", position: { x: 0, y: 0 }, data: { label: "Start" } },
    { id: "e", type: "end", position: { x: 400, y: 0 }, data: { label: "End" } },
  ],
  edges: [{ id: "e1", source: "s", target: "e" }],
  isTemplate: false,
  isPublic: true,
  isProduction: true,
  externalSlug: `pw-invoke-${crypto.randomBytes(4).toString("hex")}`,
};

const TERMINAL_STATUSES = new Set(["completed", "failed", "error"]);
const POLL_INTERVAL_MS = 1_500;
const POLL_TIMEOUT_MS = 30_000;

test("POST /api/run/{slug} with API key returns executionId and runs to completion", async ({
  request,
}) => {
  // 1. Register user
  const email = `pw-ext-${crypto.randomBytes(4).toString("hex")}@example.com`;
  const password = "correct-horse-battery-staple";

  const regRes = await request.post(`${apiUrl}/auth/register`, {
    data: { email, password, displayName: "Playwright External" },
  });
  expect(regRes.status()).toBe(201);
  const regBody = (await regRes.json()) as { id: string; accessToken: string };
  const userToken = regBody.accessToken;

  // 2. Create API key
  const keyRes = await request.post(`${apiUrl}/api-keys`, {
    data: { label: "pw-external-invoke-test" },
    headers: { Authorization: `Bearer ${userToken}` },
  });
  expect(keyRes.status()).toBe(201);
  const keyBody = (await keyRes.json()) as { key: string };
  const apiKey = keyBody.key;
  // API key format is ck_<...>
  expect(apiKey).toMatch(/^ck_/);

  // 3. Create and publish a workflow
  const wfRes = await request.post(`${apiUrl}/workflows`, {
    data: MINIMAL_PUBLISHED_WORKFLOW,
    headers: { Authorization: `Bearer ${userToken}` },
  });
  expect(wfRes.status()).toBe(201);
  const wfBody = (await wfRes.json()) as { id: string; externalSlug: string | null };
  const slug = wfBody.externalSlug ?? MINIMAL_PUBLISHED_WORKFLOW.externalSlug;
  expect(slug).toBeTruthy();

  // 4. POST /api/run/{slug} with API key
  const invokeRes = await request.post(`${apiUrl}/api/run/${slug}`, {
    data: { inputs: {} },
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  // Accept 200 or 202 (accepted for async execution)
  expect([200, 202]).toContain(invokeRes.status());

  const invokeBody = (await invokeRes.json()) as { executionId: string };
  expect(invokeBody.executionId).toBeTruthy();
  const executionId = invokeBody.executionId;

  // 5. Poll until terminal status
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let finalStatus = "";

  while (Date.now() < deadline) {
    const pollRes = await request.get(`${apiUrl}/executions/${executionId}`, {
      headers: { Authorization: `Bearer ${userToken}` },
    });
    expect(pollRes.status()).toBe(200);
    const pollBody = (await pollRes.json()) as { status: string };
    finalStatus = pollBody.status;

    if (TERMINAL_STATUSES.has(finalStatus)) break;

    // Wait before next poll
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }

  // A Start→End workflow should always complete (not fail)
  expect(finalStatus).toBe("completed");
});
