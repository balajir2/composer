"use client";

import "reactflow/dist/style.css";

import { useEffect } from "react";
import ReactFlow, {
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node as RFNode,
  type Edge as RFEdge,
  type NodeTypes,
} from "reactflow";

// ---------------------------------------------------------------------------
// GenericNode — stub renderer for all Composer node types.
// Task 20 replaces each entry with a property-aware component.
// ---------------------------------------------------------------------------

function GenericNode({ data }: { data: { label?: string } }) {
  return (
    <div className="bg-card min-w-[160px] rounded-md border px-3 py-2 shadow-sm">
      <div className="text-sm font-medium">{data.label ?? "Node"}</div>
    </div>
  );
}

export const COMPOSER_NODE_TYPES: NodeTypes = {
  start: GenericNode,
  end: GenericNode,
  agent: GenericNode,
  mcp: GenericNode,
  http: GenericNode,
  "set-state": GenericNode,
  transform: GenericNode,
  "data-transform": GenericNode,
  extract: GenericNode,
  "if-else": GenericNode,
  while: GenericNode,
  "user-approval": GenericNode,
  "join-chunks": GenericNode,
  note: GenericNode,
  guardrails: GenericNode,
  "gamma-ai": GenericNode,
  arcade: GenericNode,
  "vector-db": GenericNode,
};

// ---------------------------------------------------------------------------
// WorkflowCanvas
// ---------------------------------------------------------------------------

interface WorkflowCanvasProps {
  initialNodes: RFNode[];
  initialEdges: RFEdge[];
  /** Called on every render with the current nodes so the parent can track state. */
  onNodesChange?: (nodes: RFNode[]) => void;
  /** Called on every render with the current edges so the parent can track state. */
  onEdgesChange?: (edges: RFEdge[]) => void;
}

export function WorkflowCanvas({
  initialNodes,
  initialEdges,
  onNodesChange,
  onEdgesChange,
}: WorkflowCanvasProps) {
  const [nodes, , handleNodesChange] = useNodesState(initialNodes);
  const [edges, , handleEdgesChange] = useEdgesState(initialEdges);

  // Keep the parent's refs in sync after each render cycle.
  useEffect(() => {
    onNodesChange?.(nodes);
  }, [nodes, onNodesChange]);

  useEffect(() => {
    onEdgesChange?.(edges);
  }, [edges, onEdgesChange]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={handleNodesChange}
      onEdgesChange={handleEdgesChange}
      nodeTypes={COMPOSER_NODE_TYPES}
      fitView
      className="h-full w-full"
    >
      <Background />
      <Controls />
    </ReactFlow>
  );
}
