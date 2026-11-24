/**
 * Detecting the backend's "workflow changed since you loaded it" 409.
 *
 * A save or builder message may carry `expectedUpdatedAt`, the `updatedAt` the
 * client last saw. When someone else saved in between, the API answers 409 with
 * `{ detail: { code: "workflow_changed", message, currentUpdatedAt } }`. Other
 * 409s (for example a slug clash) have a string detail and are not matched.
 *
 * Duck-typed on `status` / `detail` so it needs no import of the API client.
 */
export type WorkflowChanged = {
  /** When the workflow last changed, as the server reported it; null if absent. */
  currentUpdatedAt: string | null;
};

export function workflowChanged(err: unknown): WorkflowChanged | null {
  if (typeof err !== "object" || err === null) return null;
  const { status, detail: body } = err as { status?: unknown; detail?: unknown };
  if (status !== 409 || typeof body !== "object" || body === null) return null;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail !== "object" || detail === null) return null;
  const { code, currentUpdatedAt } = detail as { code?: unknown; currentUpdatedAt?: unknown };
  if (code !== "workflow_changed") return null;
  return { currentUpdatedAt: typeof currentUpdatedAt === "string" ? currentUpdatedAt : null };
}
