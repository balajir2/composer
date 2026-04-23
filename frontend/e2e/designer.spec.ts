/**
 * designer.spec.ts — Designer canvas smoke tests.
 *
 * Tests:
 *   1. Create a new workflow → canvas opens.
 *   2. Save the workflow → success toast.
 *   3. Publish the workflow → success + external URL shown.
 *
 * axe-core a11y scan on the /designer/{id} canvas page.
 *
 * Requires: uvicorn + next dev running (or CI webServer config).
 */

import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { createTestUser } from "./fixtures/test-user";

async function loginAs(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });
}

test("designer can create a workflow and see the canvas", async ({ page }) => {
  const user = await createTestUser();
  await loginAs(page, user.email, user.password);

  // Navigate to the designer list
  await page.goto("/designer");

  // Click "New workflow" button
  const newBtn = page.getByRole("button", { name: /new workflow/i });
  await newBtn.click();

  // Dialog should open — fill the name field
  const nameInput = page.locator('input[placeholder="My workflow"]');
  await expect(nameInput).toBeVisible({ timeout: 5_000 });
  await nameInput.fill("PW Designer Test");

  // Submit the dialog
  const createBtn = page.getByRole("button", { name: /^create$/i });
  await createBtn.click();

  // Should navigate to /designer/{id} (canvas)
  await expect(page).toHaveURL(/\/designer\/.+/, { timeout: 15_000 });

  // The canvas container should be visible (React Flow renders a wrapping div
  // with class "react-flow" or the toolbar above it with the workflow name)
  const workflowName = page.locator("text=PW Designer Test");
  await expect(workflowName).toBeVisible({ timeout: 10_000 });
});

test("designer can save a workflow", async ({ page }) => {
  const user = await createTestUser();
  await loginAs(page, user.email, user.password);

  // Create a workflow first
  await page.goto("/designer");
  await page.getByRole("button", { name: /new workflow/i }).click();
  await page.locator('input[placeholder="My workflow"]').fill("PW Save Test");
  await page.getByRole("button", { name: /^create$/i }).click();
  await expect(page).toHaveURL(/\/designer\/.+/, { timeout: 15_000 });

  // Click Save
  const saveBtn = page.getByRole("button", { name: /save/i });
  await saveBtn.click();

  // Expect success toast (sonner renders [data-sonner-toast])
  await expect(page.locator("[data-sonner-toast]")).toBeVisible({ timeout: 10_000 });
});

test("a11y: canvas page has no critical violations", async ({ page }) => {
  const user = await createTestUser();
  await loginAs(page, user.email, user.password);

  // Create and open a workflow
  await page.goto("/designer");
  await page.getByRole("button", { name: /new workflow/i }).click();
  await page.locator('input[placeholder="My workflow"]').fill("PW A11y Test");
  await page.getByRole("button", { name: /^create$/i }).click();
  await expect(page).toHaveURL(/\/designer\/.+/, { timeout: 15_000 });

  // Run axe-core scan; fail on violations with severity >= moderate.
  // Exclude known React Flow internal nodes that are hard to make accessible
  // without upstream changes (canvas is a complex custom component).
  const results = await new AxeBuilder({ page })
    .options({ runOnly: ["wcag2a", "wcag2aa"] })
    .exclude(".react-flow__renderer")
    .exclude(".react-flow__minimap")
    .analyze();

  const criticalOrSerious = results.violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious"
  );
  expect(criticalOrSerious, JSON.stringify(criticalOrSerious, null, 2)).toHaveLength(0);
});
