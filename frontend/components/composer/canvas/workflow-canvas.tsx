"use client";

import "reactflow/dist/style.css";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type Node as RFNode,
  type Edge as RFEdge,
  type NodeTypes,
  type NodeMouseHandler,
  type NodeProps,
  type ReactFlowInstance,
} from "reactflow";
import { ToolsPalette } from "./tools-palette";
import { PropertyPanel } from "./property-panel";
import type { PaletteDragData } from "./tools-palette";
import type { DesignerExecutionState } from "./designer-execution-panel";

// ---------------------------------------------------------------------------
// Node variants — all render the same visual box but with different handles.
// Start: source-only.  End: target-only.  All others: both.
// ---------------------------------------------------------------------------

function NodeBox({ label }: { label?: string }) {
  return (
    <div className="min-w-[160px] rounded-md border bg-card px-3 py-2 shadow-sm">
      <div className="text-sm font-medium">{label ?? "Node"}</div>
    </div>
  );
}

function StartNode({ data }: NodeProps<{ label?: string }>) {
  return (
    <>
      <NodeBox label={data.label ?? "Start"} />
      <Handle type="source" position={Position.Right} />
    </>
  );
}

function EndNode({ data }: NodeProps<{ label?: string }>) {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <NodeBox label={data.label ?? "End"} />
    </>
  );
}

function InnerNode({ data }: NodeProps<{ label?: string }>) {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <NodeBox label={data.label} />
      <Handle type="source" position={Position.Right} />
    </>
  );
}

export const COMPOSER_NODE_TYPES: NodeTypes = {
  start: StartNode,
  end: EndNode,
  agent: InnerNode,
  mcp: InnerNode,
  http: InnerNode,
  "set-state": InnerNode,
  transform: InnerNode,
  "data-transform": InnerNode,
  extract: InnerNode,
  "if-else": InnerNode,
  while: InnerNode,
  "user-approval": InnerNode,
  "join-chunks": InnerNode,
  note: InnerNode,
  guardrails: InnerNode,
  "gamma-ai": InnerNode,
  arcade: InnerNode,
  "vector-db": InnerNode,
};

// ---------------------------------------------------------------------------
// ID generator
// ---------------------------------------------------------------------------
//
// IDs are used as the node's stable handle in edges AND as the canonical
// variable-substitution key (e.g. `{{agent_1.field}}`).  Friendly
// per-type sequential IDs (agent-1, agent-2, http-1, …) are much nicer
// than opaque `dropped-1777011314708-1` strings.
//
// Uniqueness: scan the existing canvas for IDs starting with the same
// type prefix, take the highest suffix number, add one.  Works even when
// the user deletes nodes from the middle (next insert jumps past gaps).
function nextNodeId(type: string, existing: { id: string }[]): string {
  const safeType = type || "node";
  let max = 0;
  const pattern = new RegExp(`^${safeType}-(\\d+)$`);
  for (const n of existing) {
    const m = pattern.exec(n.id);
    if (m && m[1]) {
      const n2 = parseInt(m[1], 10);
      if (Number.isFinite(n2) && n2 > max) max = n2;
    }
  }
  return `${safeType}-${max + 1}`;
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
  /** Live draft-run state — each node's className reflects
   *  running / completed / failed so designers see progress in-canvas. */
  runState?: DesignerExecutionState;
}

export function WorkflowCanvas({
  initialNodes,
  initialEdges,
  onNodesChange,
  onEdgesChange,
  runState,
}: WorkflowCanvasProps) {
  const [nodes, setNodes, handleNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, handleEdgesChange] = useEdgesState(initialEdges);

  // Apply run-state decorations to node.className whenever runState changes.
  useEffect(() => {
    if (!runState) {
      setNodes((nds) =>
        nds.map((n) =>
          n.className?.startsWith("composer-node-")
            ? { ...n, className: undefined }
            : n
        )
      );
      return;
    }
    const { byNodeId, currentNodeId } = runState;
    setNodes((nds) =>
      nds.map((n) => {
        const isCurrent = currentNodeId === n.id;
        const r = byNodeId[n.id];
        const next = isCurrent
          ? "composer-node-running"
          : r?.status === "failed"
            ? "composer-node-failed"
            : r?.status === "completed"
              ? "composer-node-completed"
              : r?.status === "running"
                ? "composer-node-running"
                : undefined;
        if (n.className === next) return n;
        return { ...n, className: next };
      })
    );
  }, [runState, setNodes]);

  // onConnect — ReactFlow calls this when the user drags a connection
  // between two handles.  addEdge appends a new RFEdge with a unique id.
  const handleConnect = useCallback(
    (params: Connection) => {
      setEdges((prev) =>
        addEdge(
          { ...params, id: `edge-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` },
          prev
        )
      );
    },
    [setEdges]
  );

  // Selected node id — drives the right-panel.
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Context menu state — shown on right-click of a node or edge.
  const [contextMenu, setContextMenu] = useState<{
    kind: "node" | "edge";
    id: string;
    x: number;
    y: number;
  } | null>(null);

  // Ref to the ReactFlow wrapper div for coordinate math on drop.
  const rfWrapperRef = useRef<HTMLDivElement>(null);

  // Captured on ReactFlow's onInit — used to project pixel coords into flow
  // coords (accounts for pan + zoom) for drop-position calculations.
  const rfInstanceRef = useRef<ReactFlowInstance | null>(null);

  // Close the context menu on Escape or any outside click.
  useEffect(() => {
    if (!contextMenu) return;
    function close() {
      setContextMenu(null);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [contextMenu]);

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

  // ── Right-click context menu ───────────────────────────────────────────────

  const handleNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: RFNode) => {
      event.preventDefault();
      const rect = rfWrapperRef.current?.getBoundingClientRect();
      setContextMenu({
        kind: "node",
        id: node.id,
        x: rect ? event.clientX - rect.left : event.clientX,
        y: rect ? event.clientY - rect.top : event.clientY,
      });
    },
    []
  );

  const handleEdgeContextMenu = useCallback(
    (event: React.MouseEvent, edge: RFEdge) => {
      event.preventDefault();
      const rect = rfWrapperRef.current?.getBoundingClientRect();
      setContextMenu({
        kind: "edge",
        id: edge.id,
        x: rect ? event.clientX - rect.left : event.clientX,
        y: rect ? event.clientY - rect.top : event.clientY,
      });
    },
    []
  );

  function handleContextDelete() {
    if (!contextMenu) return;
    if (contextMenu.kind === "node") {
      const id = contextMenu.id;
      setNodes((prev) => prev.filter((n) => n.id !== id));
      setEdges((prev) => prev.filter((e) => e.source !== id && e.target !== id));
      if (selectedNodeId === id) setSelectedNodeId(null);
    } else {
      const id = contextMenu.id;
      setEdges((prev) => prev.filter((e) => e.id !== id));
    }
    setContextMenu(null);
  }

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

    // Compute drop position in flow coordinates.  Raw pixel coords need to be
    // projected through ReactFlow's viewport transform (pan/zoom), otherwise
    // nodes land wherever the default transform puts them — often far off-screen.
    const wrapper = rfWrapperRef.current;
    const rect = wrapper?.getBoundingClientRect();
    const pixelX = rect ? e.clientX - rect.left : e.clientX;
    const pixelY = rect ? e.clientY - rect.top : e.clientY;
    const instance = rfInstanceRef.current;
    const projected = instance
      ? instance.project({ x: pixelX, y: pixelY })
      : { x: pixelX, y: pixelY };
    const x = projected.x;
    const y = projected.y;

    // Resolve the target node type FIRST so the ID generator can use it
    // as the prefix (agent-1, http-2, mcp-1, …) instead of an opaque
    // `dropped-<timestamp>-<n>` string.
    const targetType =
      dragData.kind === "node"
        ? dragData.nodeType
        : dragData.kind === "builtin"
          ? "agent"
          : dragData.kind === "mcp"
            ? "mcp"
            : "node";
    const id = nextNodeId(targetType, nodes);
    let newNode: RFNode | null = null;

    if (dragData.kind === "node") {
      newNode = {
        id,
        type: dragData.nodeType,
        position: { x, y },
        data: { label: dragData.label },
      };
    } else if (dragData.kind === "builtin") {
      newNode = {
        id,
        type: "agent",
        position: { x, y },
        data: { label: `Agent (${dragData.label})`, tools: [dragData.id] },
      };
    } else if (dragData.kind === "mcp") {
      newNode = {
        id,
        type: "mcp",
        position: { x, y },
        data: { label: dragData.name, serverId: dragData.id },
      };
    }

    if (newNode) {
      setNodes((prev) => [...prev, newNode as RFNode]);
      // Auto-fit on next frame so the new node is definitely visible.
      // We wait one animation frame so ReactFlow's internal store has
      // committed the new node before fitView reads node bounds.
      requestAnimationFrame(() => {
        rfInstanceRef.current?.fitView({ padding: 0.2, duration: 400 });
      });
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
        className="relative flex-1 overflow-hidden"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onInit={(instance) => {
            rfInstanceRef.current = instance;
          }}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onConnect={handleConnect}
          onNodeClick={handleNodeClick}
          onNodeContextMenu={handleNodeContextMenu}
          onEdgeContextMenu={handleEdgeContextMenu}
          onPaneClick={() => {
            setSelectedNodeId(null);
            setContextMenu(null);
          }}
          deleteKeyCode={["Delete", "Backspace"]}
          nodeTypes={COMPOSER_NODE_TYPES}
          fitView
          className="h-full w-full"
        >
          <Background />
          <Controls />
        </ReactFlow>

        {/* Right-click context menu */}
        {contextMenu && (
          <div
            className="absolute z-50 min-w-[140px] rounded-md border bg-popover text-popover-foreground shadow-md"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={handleContextDelete}
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground"
            >
              Delete {contextMenu.kind}
            </button>
          </div>
        )}
      </div>

      {/* Right sidebar — Property panel */}
      {selectedNode && (
        <PropertyPanel
          node={selectedNode}
          allNodes={nodes}
          onChange={handlePanelChange}
          onClose={() => setSelectedNodeId(null)}
        />
      )}
    </div>
  );
}
