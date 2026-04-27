/**
 * Client-side workflow import/export utilities.
 *
 * Two interchange formats:
 *
 *   - **JSON**: the workflow envelope verbatim — exactly what
 *     `POST /workflows` accepts.  Round-trips losslessly.
 *
 *   - **Markdown**: a human-readable doc (name, description, node summary,
 *     edges) plus a `json` code-fenced block at the bottom containing the
 *     full workflow JSON.  The doc reads cleanly in any markdown viewer
 *     and the embedded JSON makes import a regex extract.
 *
 * Both work without backend changes — import calls the existing
 * `createWorkflow()` helper, export downloads via Blob.
 */

import type { components } from "./api/generated/schema";

type WorkflowRead = components["schemas"]["WorkflowRead"];
type WorkflowCreate = components["schemas"]["WorkflowCreate"];

/** Minimal shape we accept on import — maps onto WorkflowCreate. */
export type WorkflowImportPayload = {
  name: string;
  description?: string | null;
  category?: string | null;
  tags?: string[];
  difficulty?: string | null;
  estimatedTime?: string | null;
  nodes: unknown[];
  edges: unknown[];
  version?: string | null;
  isTemplate?: boolean;
  isPublic?: boolean;
};

// ─── Export ────────────────────────────────────────────────────────────

function trimWorkflowForExport(wf: WorkflowRead): WorkflowImportPayload {
  // Strip server-managed fields (id, userId, timestamps, isProduction,
  // externalSlug) so the exported JSON is a clean import payload.
  return {
    name: wf.name,
    description: wf.description ?? null,
    category: wf.category ?? null,
    tags: wf.tags ?? [],
    difficulty: wf.difficulty ?? null,
    estimatedTime: wf.estimatedTime ?? null,
    nodes: (wf.nodes as unknown as unknown[]) ?? [],
    edges: (wf.edges as unknown as unknown[]) ?? [],
    version: wf.version ?? null,
    isTemplate: Boolean(wf.isTemplate),
    isPublic: Boolean(wf.isPublic),
  };
}

export function exportWorkflowAsJson(wf: WorkflowRead): string {
  return JSON.stringify(trimWorkflowForExport(wf), null, 2);
}

function nodeOneLineSummary(n: Record<string, unknown>): string {
  const id = String(n.id ?? "");
  const type = String(n.type ?? "node");
  const data = (n.data as Record<string, unknown>) ?? {};
  const nodeName = typeof data.nodeName === "string" ? data.nodeName : "";
  const label = typeof data.label === "string" ? data.label : "";
  const display = nodeName || label || id;
  return `- **${display}** \`${id}\` (type: \`${type}\`)`;
}

export function exportWorkflowAsMarkdown(wf: WorkflowRead): string {
  const trimmed = trimWorkflowForExport(wf);
  const nodes = trimmed.nodes as Array<Record<string, unknown>>;
  const edges = trimmed.edges as Array<Record<string, unknown>>;
  const lines: string[] = [];

  lines.push(`# ${wf.name}`);
  if (wf.description) {
    lines.push("");
    lines.push(`> ${wf.description}`);
  }
  lines.push("");

  // Configuration block — only emit non-empty fields to keep the file
  // tidy when the workflow doesn't use them.
  const config: string[] = [];
  if (wf.category) config.push(`- **Category:** ${wf.category}`);
  if (wf.tags && wf.tags.length > 0)
    config.push(`- **Tags:** ${wf.tags.join(", ")}`);
  if (wf.difficulty) config.push(`- **Difficulty:** ${wf.difficulty}`);
  if (wf.estimatedTime) config.push(`- **Estimated time:** ${wf.estimatedTime}`);
  if (wf.version) config.push(`- **Version:** ${wf.version}`);
  if (config.length > 0) {
    lines.push("## Configuration");
    lines.push("");
    lines.push(...config);
    lines.push("");
  }

  if (nodes.length > 0) {
    lines.push(`## Nodes (${nodes.length})`);
    lines.push("");
    for (const n of nodes) lines.push(nodeOneLineSummary(n));
    lines.push("");
  }

  if (edges.length > 0) {
    lines.push(`## Edges (${edges.length})`);
    lines.push("");
    for (const e of edges) {
      const src = String(e.source ?? "?");
      const tgt = String(e.target ?? "?");
      const branch = e.branch ? ` _(branch: ${String(e.branch)})_` : "";
      lines.push(`- \`${src}\` → \`${tgt}\`${branch}`);
    }
    lines.push("");
  }

  // Embedded JSON for round-trip.  Keep this LAST so casual readers see
  // the doc first, and the importer can find the fence reliably.
  lines.push("## Workflow JSON");
  lines.push("");
  lines.push("```json");
  lines.push(JSON.stringify(trimmed, null, 2));
  lines.push("```");
  lines.push("");

  return lines.join("\n");
}

export function downloadString(
  filename: string,
  mime: string,
  content: string
): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ─── Import ────────────────────────────────────────────────────────────

const FENCE_PATTERN = /```(?:json|JSON)?\s*\n([\s\S]*?)\n```/;

export function parseWorkflowFromText(
  text: string,
  filename: string
): WorkflowImportPayload {
  const lower = filename.toLowerCase();
  const looksJson = lower.endsWith(".json") || text.trim().startsWith("{");
  const looksMarkdown = lower.endsWith(".md") || lower.endsWith(".markdown");

  let jsonText = text.trim();

  if (looksMarkdown && !looksJson) {
    // Pull the first fenced JSON block out of the markdown.  The
    // exporter always writes one; older or hand-written markdown that
    // doesn't have one fails with a clear message below.
    const m = text.match(FENCE_PATTERN);
    if (!m || !m[1]) {
      throw new Error(
        "No fenced ```json``` block found in the markdown — import expects " +
          "the same structure exportWorkflowAsMarkdown produces."
      );
    }
    jsonText = m[1].trim();
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonText);
  } catch (e) {
    throw new Error(
      "File doesn't contain valid JSON: " +
        (e instanceof Error ? e.message : String(e))
    );
  }

  if (!parsed || typeof parsed !== "object") {
    throw new Error("Imported file isn't a JSON object.");
  }

  const obj = parsed as Record<string, unknown>;
  const name = typeof obj.name === "string" ? obj.name : "";
  if (!name.trim()) {
    throw new Error("Imported workflow is missing a `name`.");
  }
  const nodes = Array.isArray(obj.nodes) ? obj.nodes : null;
  const edges = Array.isArray(obj.edges) ? obj.edges : null;
  if (!nodes || !edges) {
    throw new Error("Imported workflow must have `nodes` and `edges` arrays.");
  }

  return {
    name: String(name),
    description: typeof obj.description === "string" ? obj.description : null,
    category: typeof obj.category === "string" ? obj.category : null,
    tags: Array.isArray(obj.tags) ? (obj.tags as string[]) : [],
    difficulty: typeof obj.difficulty === "string" ? obj.difficulty : null,
    estimatedTime:
      typeof obj.estimatedTime === "string" ? obj.estimatedTime : null,
    nodes,
    edges,
    version: typeof obj.version === "string" ? obj.version : null,
    isTemplate: Boolean(obj.isTemplate),
    isPublic: Boolean(obj.isPublic),
  };
}

/** Convert the parsed import payload into the exact body createWorkflow expects. */
export function toWorkflowCreate(p: WorkflowImportPayload): WorkflowCreate {
  return {
    name: p.name,
    description: p.description ?? null,
    category: p.category ?? null,
    tags: p.tags ?? [],
    difficulty: p.difficulty ?? null,
    estimatedTime: p.estimatedTime ?? null,
    nodes: p.nodes as WorkflowCreate["nodes"],
    edges: p.edges as WorkflowCreate["edges"],
    version: p.version ?? null,
    isTemplate: Boolean(p.isTemplate),
    isPublic: Boolean(p.isPublic),
    isProduction: null,
    externalSlug: null,
  };
}
