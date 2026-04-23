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
};

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
      sourceHandle: e.sourceHandle ?? undefined,
      label: e.label ?? undefined,
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
  return {
    nodes: rfNodes.map((n) => ({
      id: n.id,
      type: n.type ?? "start",
      position: n.position,
      data: (n.data as Record<string, unknown>) ?? {},
    })),
    edges: rfEdges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? null,
      label: (e.label as string | undefined) ?? null,
    })),
  };
}
