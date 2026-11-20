// 2026-11-19 defect: the builder reads the workflow from the database while
// canvas edits autosave on a 3 s debounce. Deleting an edge and messaging the
// builder inside that window made it rebuild from (and save over) the stale
// graph, so the deleted edge came back. The panel must save first.
import { test, expect, type Page } from "@playwright/test";
import { createTestUser, cleanupTestUsers, type TestUser } from "./fixtures/test-user";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

test.afterEach(async () => {
  await cleanupTestUsers();
});

async function loginAs(page: Page, user: TestUser) {
  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });
}

async function seedWorkflow(page: Page, user: TestUser, edges: object[]): Promise<string> {
  const res = await page.request.post(`${apiUrl}/workflows`, {
    headers: { Authorization: `Bearer ${user.accessToken}` },
    data: {
      name: "PW Builder Race",
      isTemplate: false,
      isPublic: false,
      nodes: [
        { id: "start-1", type: "start", position: { x: 0, y: 0 }, data: { label: "Start" } },
        { id: "end-1", type: "end", position: { x: 400, y: 0 }, data: { label: "End" } },
        { id: "note-1", type: "note", position: { x: 200, y: 200 }, data: { label: "Note" } },
      ],
      edges,
    },
  });
  expect(res.status()).toBe(201);
  return ((await res.json()) as { id: string }).id;
}

// React Flow v11 tags each edge with data-testid="rf__edge-<id>".
const edgeSelector = (edgeId: string) => `[data-testid="rf__edge-${edgeId}"]`;

test("unsaved edge deletion is saved before the builder runs", async ({ page }) => {
  const user = await createTestUser();
  // The start -> note edge is ignored by the end-node and reachability rules,
  // so deleting it leaves a valid graph and the save succeeds.
  const id = await seedWorkflow(page, user, [
    { id: "e1", source: "start-1", target: "end-1" },
    { id: "e-note", source: "start-1", target: "note-1" },
  ]);
  const order: string[] = [];
  let savedEdges: unknown[] | null = null;
  page.on("request", (req) => {
    if (req.method() === "PUT" && new RegExp(`/workflows/${id}$`).test(req.url())) {
      order.push("save");
      savedEdges = (req.postDataJSON() as { edges: unknown[] }).edges;
    }
    if (req.method() === "POST" && req.url().includes(`/builder/${id}/message`)) {
      order.push("builder");
    }
  });
  await page.route(`**/builder/${id}/message`, (route) =>
    route.fulfill({
      json: { sessionId: "s", events: [], reply: "noted", workflow: null, validation_errors: [] },
    })
  );

  await loginAs(page, user);
  await page.goto(`/designer/${id}`);
  const noteEdge = page.locator(edgeSelector("e-note"));
  await expect(noteEdge).toBeVisible({ timeout: 15_000 });
  await noteEdge.click({ force: true });
  await page.keyboard.press("Delete");

  // Well inside the 3 s autosave debounce.
  const input = page.getByPlaceholder("Describe your workflow…");
  await input.fill("tidy up the edges");
  await input.press("Enter");
  await expect(page.getByText("noted")).toBeVisible({ timeout: 10_000 });

  expect(order[0]).toBe("save");
  expect(order).toContain("builder");
  expect(savedEdges).toHaveLength(1);
});

test("a failed save blocks the builder instead of building on stale data", async ({ page }) => {
  const user = await createTestUser();
  const id = await seedWorkflow(page, user, [{ id: "e1", source: "start-1", target: "end-1" }]);
  let builderCalled = false;
  await page.route(`**/builder/${id}/message`, (route) => {
    builderCalled = true;
    return route.fulfill({
      json: { sessionId: "s", events: [], reply: "x", workflow: null, validation_errors: [] },
    });
  });

  await loginAs(page, user);
  await page.goto(`/designer/${id}`);
  const edge = page.locator(edgeSelector("e1"));
  await expect(edge).toBeVisible({ timeout: 15_000 });
  await edge.click({ force: true });
  await page.keyboard.press("Delete"); // End now has no incoming edge: save gets 422

  const input = page.getByPlaceholder("Describe your workflow…");
  await input.fill("rebuild it");
  await input.press("Enter");
  await expect(page.getByText(/couldn't save your latest canvas edits/i)).toBeVisible({
    timeout: 10_000,
  });
  expect(builderCalled).toBe(false);
});
