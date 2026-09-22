import type { Node as RFNode, Edge as RFEdge } from "reactflow";

export type ComposerNode = {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, unknown>;
};

export type ComposerEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  label?: string | null;
  /**
   * Branch label, required by the backend when the source node is a
   * conditional type (`if-else` → "true"/"false", `while` →
   * "body"/"exit", `user-approval` → "approved"/"rejected"; null
   * otherwise).  Mirrors `WorkflowEdge.branch` in
   * src/engine/workflow.py and the validator in `_branch_mapping`.
   */
  branch?: string | null;
};

const CONDITIONAL_SOURCE_TYPES = new Set([
  "if-else",
  "while",
  "user-approval",
  "decision",
]);

export function toReactFlow(
  nodes: ComposerNode[],
  edges: ComposerEdge[]
): {
  rfNodes: RFNode[];
  rfEdges: RFEdge[];
} {
  return {
    rfNodes: nodes.map((n) => ({
      id: n.id,
      type: n.type,
      position: n.position,
      data: { ...n.data },
    })),
    rfEdges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      // Back-compat: older saves only set `branch` (the canvas had no
      // labelled handles), so seed `sourceHandle` from `branch` when
      // sourceHandle is absent.  This makes the labelled handles light
      // up correctly when a previously-saved workflow is reopened.
      sourceHandle: e.sourceHandle ?? e.branch ?? undefined,
      label: e.label ?? e.branch ?? undefined,
    })),
  };
}

export function fromReactFlow(
  rfNodes: RFNode[],
  rfEdges: RFEdge[]
): {
  nodes: ComposerNode[];
  edges: ComposerEdge[];
} {
  // Build a lookup so we can derive `branch` per outgoing edge from the
  // source node's type — branch is only valid (and required) when the
  // source is a conditional node.
  const nodeTypeById = new Map<string, string>(
    rfNodes.map((n) => [n.id, n.type ?? ""])
  );
  return {
    nodes: rfNodes.map((n) => ({
      id: n.id,
      type: n.type ?? "start",
      position: n.position,
      data: (n.data as Record<string, unknown>) ?? {},
    })),
    edges: rfEdges.map((e) => {
      const sourceType = nodeTypeById.get(e.source) ?? "";
      const sourceHandle = e.sourceHandle ?? null;
      // `sourceHandle` IS the branch label when the source is conditional —
      // BranchingNode in workflow-canvas.tsx assigns id="true"/"false"/etc
      // to each handle.  For non-conditional sources, branch must be null
      // or the backend rejects the edge ("has branch=… but its source is
      // not a conditional node").
      const branch =
        CONDITIONAL_SOURCE_TYPES.has(sourceType) &&
        typeof sourceHandle === "string" &&
        sourceHandle.length > 0
          ? sourceHandle
          : null;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle,
        label: (e.label as string | undefined) ?? null,
        branch,
      };
    }),
  };
}
