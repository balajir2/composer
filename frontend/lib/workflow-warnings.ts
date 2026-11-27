import type { components } from "@/lib/api/generated/schema";

/** A non-blocking problem the server reports on a saved canvas (it saves, but cannot run yet). */
export type WorkflowWarning = components["schemas"]["WorkflowWarning"];

/** Messages per node id, for badging the offending node on the canvas. */
export function warningsByNode(
  warnings: readonly WorkflowWarning[] | undefined
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const w of warnings ?? []) {
    (out[w.nodeId] ??= []).push(w.message);
  }
  return out;
}
