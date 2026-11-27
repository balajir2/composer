import type { components } from "@/lib/api/generated/schema";

/** A non-blocking problem the server reports on a saved canvas (it saves, but cannot run yet). */
export type WorkflowWarning = components["schemas"]["WorkflowWarning"];

/**
 * Warning codes the server also enforces at run, publish and resume: a canvas with one of
 * these saves but cannot run yet. Other codes (e.g. `unknown_variable`) only advise.
 */
const BLOCKING_CODES: ReadonlySet<WorkflowWarning["code"]> = new Set([
  "missing_branch",
  "join_mixed_inputs",
  "join_lane_escapes",
  "join_collision",
]);

export function blocksRun(warning: WorkflowWarning): boolean {
  return BLOCKING_CODES.has(warning.code);
}

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
