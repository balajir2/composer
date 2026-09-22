/**
 * decision-node.spec.ts — Decision node (binary mode) end-to-end smoke test.
 *
 * Drives the real Designer UI: signs in, creates a workflow, drags a
 * Decision node and two End nodes onto the canvas, configures the Decision
 * node's mode/instruction/example via its property panel, wires the
 * true/false branches to the two End nodes, saves, runs the draft, and
 * asserts the execution actually took the "true" branch.
 *
 * Selectors below were captured by hand-driving a live `next dev` +
 * `uvicorn` instance of this app (not guessed), since no existing e2e spec
 * in this repo exercises the Designer's drag-and-drop palette, node
 * property panels, or handle-to-handle wiring — see the Task 15 report for
 * details. Notable, hard-won mechanics this test depends on:
 *
 *   - The palette's drag source uses the HTML5 native drag-and-drop API
 *     (`draggable` + `dragstart`/`drop` + `DataTransfer`), which Playwright's
 *     locator-based mouse actions (`.dragTo()`, manual mouse down/move/up)
 *     do NOT trigger. Node placement is therefore done by dispatching real
 *     `DragEvent`s with a `DataTransfer` carrying the exact
 *     "application/composer-palette" payload `tools-palette.tsx` writes
 *     (`{"kind":"node","nodeType":...,"label":...}`).
 *   - Dropping two nodes of the *same* type back-to-back in a single
 *     `page.evaluate()` call collides: `nextNodeId()` computes the new id
 *     from the current (stale, pre-render) `nodes` array, so both drops
 *     compute the same id (e.g. two nodes both claiming `end-1`) and one
 *     is lost. Each drop is therefore its own separate, awaited
 *     `page.evaluate()` call, giving React a chance to re-render between
 *     drops.
 *   - React Flow's *handle-to-handle wiring* (Start -> Decision,
 *     Decision's true/false -> the two End nodes) is done with Playwright's
 *     real `locator.dragTo()` (a genuine mouse down/move/up sequence).
 *     Click-to-connect (`connectOnClick`, React Flow's other supported
 *     wiring gesture) was tried first and reproducibly failed to complete
 *     when the source was a *labeled* branch handle (Decision's/If-Else's
 *     `true`/`false` outputs specifically) even from a clean state --
 *     `dragTo()` is the one wiring mechanism that worked reliably in
 *     manual verification, including for labeled branch handles.
 *   - The Decision property panel's Mode/Provider `<select>`s show
 *     "Binary (yes/no)" / "LLM" as selected by default, but the
 *     *underlying node data* is never populated with `mode`/`provider`
 *     unless the select's `onValueChange` actually fires. Saving a
 *     Decision node without explicitly touching those selects gets a real
 *     422 from the backend ("Field required: nodes.N.decision.data.mode").
 *     This test explicitly selects both, even though they're already the
 *     visually-selected default option, specifically to avoid that.
 *
 * Requires: uvicorn + next dev running (or CI webServer config), and a
 * real ANTHROPIC_API_KEY configured server-side — the Decision node's
 * "llm" provider (LLMJudgmentProvider) makes a real Anthropic call.
 */

import { test, expect, request as playwrightRequest } from "@playwright/test";
import { createTestUser, cleanupTestUsers } from "./fixtures/test-user";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

test.afterEach(async () => {
  await cleanupTestUsers();
});

/**
 * Drags a palette item of the given label onto the canvas at the given
 * pane-relative coordinates, by dispatching real `DragEvent`s carrying the
 * same `DataTransfer` payload `tools-palette.tsx`'s `PaletteItem` writes on
 * `dragstart` (native HTML5 drag-and-drop; not simulable via Playwright's
 * mouse-based drag helpers). Must be awaited on its own — see the module
 * docstring's note on same-tick id collisions when two nodes of the same
 * type are dropped without a render in between.
 */
async function dropPaletteNode(
  page: import("@playwright/test").Page,
  label: string,
  x: number,
  y: number
): Promise<void> {
  await page.evaluate(
    ({ label, x, y }) => {
      const paletteItems = Array.from(document.querySelectorAll('[draggable="true"]'));
      const item = paletteItems.find((el) => el.textContent?.trim() === label);
      const pane = document.querySelector(".react-flow__pane");
      if (!item || !pane) {
        throw new Error(`dropPaletteNode: could not find palette item "${label}" or canvas pane`);
      }
      const dt = new DataTransfer();
      item.dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: dt }));
      const rect = pane.getBoundingClientRect();
      const clientX = rect.left + x;
      const clientY = rect.top + y;
      pane.dispatchEvent(
        new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt, clientX, clientY })
      );
      pane.dispatchEvent(
        new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt, clientX, clientY })
      );
    },
    { label, x, y }
  );
}

test("Decision node (binary) wires both branches and the true branch executes", async ({ page }) => {
  // Generous budget: this test drives ~10 real UI interactions (drag/drop,
  // panel edits, handle wiring) plus a real Anthropic call and an
  // execution poll, well beyond the config's default 60s.
  test.setTimeout(180_000);

  const user = await createTestUser();

  // --- Sign in (matches designer.spec.ts's loginAs pattern) ---------------
  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });

  // --- Create a new blank workflow (matches designer.spec.ts) -------------
  await page.goto("/designer");
  await page.getByRole("button", { name: /new workflow/i }).click();
  await page.locator('input[placeholder="My workflow"]').fill("PW Decision Node Test");
  await page.getByRole("button", { name: /^create$/i }).click();
  await expect(page).toHaveURL(/\/designer\/.+/, { timeout: 15_000 });

  // A blank workflow starts with a Start -> End default. Remove the default
  // End node (and, with it, the default Start->End edge) so this test's own
  // End nodes are the only ones on the canvas — avoids id collisions and
  // keeps the graph unambiguous.
  await page.locator('.react-flow__node[data-id="end-1"]').click();
  await page.keyboard.press("Delete");
  await expect(page.locator('.react-flow__node[data-id="end-1"]')).toHaveCount(0);

  // --- Drag a Decision node and two End nodes onto the canvas -------------
  // Each drop is awaited separately — see dropPaletteNode's docstring.
  await dropPaletteNode(page, "Decision", 350, 150);
  await dropPaletteNode(page, "End", 650, 60);
  await dropPaletteNode(page, "End", 650, 260);

  await expect(page.getByTestId("rf__node-decision-1")).toBeVisible();
  await expect(page.getByTestId("rf__node-end-1")).toBeVisible();
  await expect(page.getByTestId("rf__node-end-2")).toBeVisible();

  // --- Configure the Decision node's property panel -----------------------
  await page.getByTestId("rf__node-decision-1").click();
  await page.locator("#node-name").fill("gate");
  // Explicitly select Mode/Provider even though "Binary (yes/no)" / "LLM"
  // are already shown as selected — the node's data.mode/data.provider are
  // only written on an actual onValueChange, and saving without ever
  // touching these selects 422s server-side. See module docstring.
  await page.locator("#decision-mode").selectOption("binary");
  await page.locator("#decision-provider").selectOption("llm");
  await page.locator("#decision-instruction").fill("Always answer yes");

  // One example: "anything" -> true. The example row's own boolean select
  // has no id/aria-label; with Mode, Provider, and Model preceding it as
  // the panel's only other <select>s, it's reliably the 4th combobox on
  // the page while exactly one example row exists.
  await page.getByRole("button", { name: "Add example" }).click();
  await page.locator('input[placeholder="Example text"]').fill("anything");
  await page.getByRole("combobox").nth(3).selectOption("true");

  // --- Name the two End nodes distinguishably ------------------------------
  await page.getByTestId("rf__node-end-1").click();
  await page.locator("#node-name").fill("end_true");

  await page.getByTestId("rf__node-end-2").click();
  await page.locator("#node-name").fill("end_false");

  // --- Wire: Start -> Decision, Decision.true -> end_true, Decision.false -> end_false ---
  // Real mouse-drag (locator.dragTo) — the one wiring gesture that worked
  // reliably in manual verification, including from labeled branch
  // handles. See module docstring for why click-to-connect was rejected.
  await page
    .locator('.react-flow__handle[data-nodeid="start-1"][data-handlepos="right"]')
    .dragTo(page.locator('.react-flow__handle[data-nodeid="decision-1"][data-handlepos="left"]'));

  await page
    .locator('.react-flow__handle[data-nodeid="decision-1"][data-handleid="true"]')
    .dragTo(page.locator('.react-flow__handle[data-nodeid="end-1"][data-handlepos="left"]'));

  await page
    .locator('.react-flow__handle[data-nodeid="decision-1"][data-handleid="false"]')
    .dragTo(page.locator('.react-flow__handle[data-nodeid="end-2"][data-handlepos="left"]'));

  await expect(page.locator('.react-flow__edge[aria-label="Edge from start-1 to decision-1"]')).toBeVisible();
  await expect(page.locator('.react-flow__edge[aria-label="Edge from decision-1 to end-1"]')).toBeVisible();
  await expect(page.locator('.react-flow__edge[aria-label="Edge from decision-1 to end-2"]')).toBeVisible();

  // --- Save --------------------------------------------------------------
  await page.getByRole("button", { name: /^save$/i }).click();
  await expect(page.locator("[data-sonner-toast]")).toBeVisible({ timeout: 10_000 });

  // --- Run the draft and capture the execution id -------------------------
  // "Run draft" only opens a dialog asking for optional JSON input (left
  // blank here); the POST /executions doesn't fire until that dialog's own
  // "Run" button is clicked, so the response wait must race *that* click,
  // not the one that opens the dialog.
  await page.getByRole("button", { name: /^run draft$/i }).click();
  const runDialogButton = page.getByRole("dialog").getByRole("button", { name: /^run$/i });
  await expect(runDialogButton).toBeVisible({ timeout: 10_000 });
  const [createExecutionResponse] = await Promise.all([
    page.waitForResponse(
      (resp) => resp.request().method() === "POST" && /\/executions$/.test(resp.url())
    ),
    runDialogButton.click(),
  ]);

  const { id: executionId } = (await createExecutionResponse.json()) as { id: string };
  expect(executionId).toBeTruthy();

  // --- Poll the backend directly for the terminal execution state ---------
  // (Same convention as external-invoke.spec.ts / date-field.spec.ts —
  // more robust than asserting on the WS-driven execution panel alone,
  // since a Decision node's "llm" provider makes a real Anthropic call and
  // its completion timing shouldn't be tied to UI polling internals.)
  const ctx = await playwrightRequest.newContext();
  type ExecutionRead = {
    status: string;
    nodeResults: Record<string, { status: string; output?: unknown }>;
  };
  let execution: ExecutionRead | null = null;
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const res = await ctx.get(`${apiUrl}/executions/${executionId}`, {
      headers: { Authorization: `Bearer ${user.accessToken}` },
    });
    expect(res.status()).toBe(200);
    execution = (await res.json()) as ExecutionRead;
    if (execution.status === "completed" || execution.status === "failed") break;
    await new Promise((r) => setTimeout(r, 1_500));
  }
  await ctx.dispose();

  expect(execution?.status).toBe("completed");
  // The Decision node ("gate", data.instruction = "Always answer yes")
  // must have decided true.
  expect(execution?.nodeResults["decision-1"]?.output).toMatchObject({ decision: true });
  // The "true" branch's End node (end_true / node id "end-1") must have
  // run to completion...
  expect(execution?.nodeResults["end-1"]?.status).toBe("completed");
  // ...and the "false" branch's End node (end_false / node id "end-2")
  // must never have run at all — EndExecutor only writes a node_results
  // entry for a node that actually executed, so its absence here is the
  // proof the wrong branch was never taken.
  expect(execution?.nodeResults["end-2"]).toBeUndefined();

  // --- Also assert at the UI level: the in-designer execution panel -------
  // The execution panel's status Badge renders the bare word "completed"
  // and nothing else on the page does, so this needs no extra scoping.
  await expect(page.getByText("completed", { exact: true }).last()).toBeVisible({ timeout: 60_000 });
  // The panel's per-node result list is the page's only <ol>/<li> list
  // (`designer-execution-panel.tsx`'s `orderedNodeEntries.map`), and only
  // gets an entry for a node that actually ran — "end_false" is always
  // visible as the *canvas node's own label* regardless of whether that
  // branch executed, so the real proof the wrong branch was never taken
  // is that it has no entry in this list, not that its label is absent
  // from the page.
  // Generous timeout: the panel's per-node entries arrive over the
  // execution's WebSocket subscription, a channel independent of (and
  // sometimes visibly slower than) this test's own direct-API polling
  // above — the backend can already report the execution "completed"
  // before this browser tab's WS delivery of the last node_completed
  // event catches up.
  const nodeResultItems = page.getByRole("listitem");
  await expect(nodeResultItems.filter({ hasText: "end_true" })).toBeVisible({ timeout: 30_000 });
  await expect(nodeResultItems.filter({ hasText: "end_false" })).toHaveCount(0);
});
