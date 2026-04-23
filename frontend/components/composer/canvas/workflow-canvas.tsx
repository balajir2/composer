"use client";

import "reactflow/dist/style.css";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node as RFNode,
  type Edge as RFEdge,
  type NodeTypes,
  type NodeMouseHandler,
} from "reactflow";
import { ToolsPalette } from "./tools-palette";
import { PropertyPanel } from "./property-panel";
import type { PaletteDragData } from "./tools-palette";

// ---------------------------------------------------------------------------
// GenericNode — visual stub for all Composer node types.
// Property behavior is handled by the PropertyPanel sidebar.
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
// ID generator
// ---------------------------------------------------------------------------
let _nodeCounter = 0;
function nextNodeId(): string {
  _nodeCounter += 1;
  return `dropped-${Date.now()}-${_nodeCounter}`;
}

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
  const [nodes, setNodes, handleNodesChange] = useNodesState(initialNodes);
  const [edges, , handleEdgesChange] = useEdgesState(initialEdges);

  // Selected node id — drives the right-panel.
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Ref to the ReactFlow wrapper div for coordinate math on drop.
  const rfWrapperRef = useRef<HTMLDivElement>(null);

  // Keep the parent's refs in sync after each render cycle.
  useEffect(() => {
    onNodesChange?.(nodes);
  }, [nodes, onNodesChange]);

  useEffect(() => {
    onEdgesChange?.(edges);
  }, [edges, onEdgesChange]);

  // ── Selection ──────────────────────────────────────────────────────────────

  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    setSelectedNodeId(node.id);
  }, []);

  const selectedNode = selectedNodeId ? nodes.find((n) => n.id === selectedNodeId) : null;

  // Patch node data while preserving existing fields.
  function handlePanelChange(patch: Record<string, unknown>) {
    if (!selectedNodeId) return;
    setNodes((prev) =>
      prev.map((n) =>
        n.id === selectedNodeId
          ? { ...n, data: { ...(n.data as Record<string, unknown>), ...patch } }
          : n
      )
    );
  }

  // ── Drop from palette ──────────────────────────────────────────────────────

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();

    const raw = e.dataTransfer.getData("application/composer-palette");
    if (!raw) return;

    let dragData: PaletteDragData;
    try {
      dragData = JSON.parse(raw) as PaletteDragData;
    } catch {
      return;
    }

    // Compute drop position relative to the ReactFlow wrapper.
    const wrapper = rfWrapperRef.current;
    const rect = wrapper?.getBoundingClientRect();
    const x = rect ? e.clientX - rect.left : e.clientX;
    const y = rect ? e.clientY - rect.top : e.clientY;

    const id = nextNodeId();

    if (dragData.kind === "node") {
      // Generic node drop — create a node of the specified type.
      const newNode: RFNode = {
        id,
        type: dragData.nodeType,
        position: { x, y },
        data: { label: dragData.label },
      };
      setNodes((prev) => [...prev, newNode]);
      setSelectedNodeId(id);
    } else if (dragData.kind === "builtin") {
      // Built-in tool → create an agent node pre-configured with the tool.
      const newNode: RFNode = {
        id,
        type: "agent",
        position: { x, y },
        data: { label: `Agent (${dragData.label})`, tools: [dragData.id] },
      };
      setNodes((prev) => [...prev, newNode]);
      setSelectedNodeId(id);
    } else if (dragData.kind === "mcp") {
      // Shared MCP → create a dedicated mcp node.
      const newNode: RFNode = {
        id,
        type: "mcp",
        position: { x, y },
        data: { label: dragData.name, serverId: dragData.id },
      };
      setNodes((prev) => [...prev, newNode]);
      setSelectedNodeId(id);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full w-full">
      {/* Left sidebar — Tools palette */}
      <ToolsPalette />

      {/* Canvas center */}
      <div
        ref={rfWrapperRef}
        className="flex-1 overflow-hidden"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onNodeClick={handleNodeClick}
          onPaneClick={() => setSelectedNodeId(null)}
          nodeTypes={COMPOSER_NODE_TYPES}
          fitView
          className="h-full w-full"
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>

      {/* Right sidebar — Property panel */}
      {selectedNode && (
        <PropertyPanel
          node={selectedNode}
          onChange={handlePanelChange}
          onClose={() => setSelectedNodeId(null)}
        />
      )}
    </div>
  );
}
