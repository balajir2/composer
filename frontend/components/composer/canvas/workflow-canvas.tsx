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
import { visualFor } from "./node-visuals";

// ---------------------------------------------------------------------------
// Node variants — n8n-inspired chips with an icon tile, name, and type
// subtitle.  Start: source-only.  End: target-only.  All others: both.
// ---------------------------------------------------------------------------

const HANDLE_STYLE: React.CSSProperties = {
  width: 12,
  height: 12,
  border: "2px solid white",
  background: "var(--brand-purple)",
  boxShadow: "0 1px 3px rgba(46, 24, 105, 0.25)",
};

type NodeData = {
  label?: string;
  nodeName?: string;
  /** Used by built-in tool drops; surfaced as a small badge on agent
   *  chips so designers can see at a glance which tool the node is
   *  pre-configured for. */
  tools?: string[] | string;
};

type NodeChipProps = {
  type: string;
  /** React Flow's auto-generated id (agent-1, http-2, …) — shown as a
   *  monospace caption when no user-provided Name is set, mirroring
   *  what {{agent_1}} resolves to in prompts. */
  id: string;
  data: NodeData;
};

function NodeChip({ type, id, data }: NodeChipProps) {
  const visual = visualFor(type);
  const Icon = visual.icon;
  // Display priority: user-given Name > generic data.label > visual default.
  const displayName =
    (typeof data.nodeName === "string" && data.nodeName) ||
    (typeof data.label === "string" && data.label) ||
    visual.label;
  // Subtitle: the type label as long as the user has set a custom Name —
  // otherwise it'd duplicate the title.
  const showSubtitle = displayName !== visual.label;

  return (
    <div
      className="composer-chip group relative flex min-w-[180px] items-center gap-3 rounded-xl border bg-card px-3 py-2.5 shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md"
      data-node-type={type}
    >
      <span
        className={`flex size-9 shrink-0 items-center justify-center rounded-lg ${visual.iconWrapClass}`}
      >
        <Icon className="size-5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className={`truncate text-sm font-semibold leading-tight ${visual.accent}`}>
          {displayName}
        </div>
        {showSubtitle && (
          <div className="truncate text-[10px] uppercase tracking-wide text-muted-foreground">
            {visual.label}
          </div>
        )}
        <div className="mt-0.5 truncate font-mono text-[9px] text-muted-foreground/70">
          {id}
        </div>
      </div>
    </div>
  );
}

function StartNode({ id, data, type }: NodeProps<NodeData>) {
  return (
    <>
      <NodeChip type={type ?? "start"} id={id} data={data} />
      <Handle
        type="source"
        position={Position.Right}
        style={{ ...HANDLE_STYLE, background: "rgb(16,185,129)" }}
      />
    </>
  );
}

function EndNode({ id, data, type }: NodeProps<NodeData>) {
  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        style={{ ...HANDLE_STYLE, background: "rgb(244,63,94)" }}
      />
      <NodeChip type={type ?? "end"} id={id} data={data} />
    </>
  );
}

function InnerNode({ id, data, type }: NodeProps<NodeData>) {
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <NodeChip type={type ?? "node"} id={id} data={data} />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </>
  );
}

/**
 * Branching node for conditional source types — `if-else`, `while`,
 * `user-approval`.  Two labelled source handles instead of one anonymous
 * handle, so each outgoing edge carries a `sourceHandle` we can save as
 * the backend's required `branch` field.  Without this, the canvas
 * would let users draw edges that fail server-side validation with
 * "leaves if-else node but has no branch label".
 */
type BranchSpec = { id: string; label: string; color: string };

const BRANCH_SPECS: Record<string, BranchSpec[]> = {
  // Backend's _branch_mapping in src/engine/graph_builder.py demands
  // these exact strings.  Don't rename without updating both sides.
  "if-else": [
    { id: "true", label: "true", color: "rgb(16,185,129)" },
    { id: "false", label: "false", color: "rgb(244,63,94)" },
  ],
  while: [
    { id: "body", label: "body", color: "rgb(59,130,246)" },
    { id: "exit", label: "exit", color: "rgb(244,63,94)" },
  ],
  "user-approval": [
    { id: "approved", label: "approved", color: "rgb(16,185,129)" },
    { id: "rejected", label: "rejected", color: "rgb(244,63,94)" },
  ],
  decision: [
    { id: "true", label: "true", color: "rgb(16,185,129)" },
    { id: "false", label: "false", color: "rgb(244,63,94)" },
  ],
};

// Decision's "choice" mode has a variable number of outgoing branches
// (one per configured option), unlike the fixed true/false pair used by
// its "binary" mode (and by if-else/while/user-approval).  Colors cycle
// through this palette so each choice branch is visually distinct.
const CHOICE_BRANCH_COLORS = [
  "rgb(59,130,246)",
  "rgb(168,85,247)",
  "rgb(234,179,8)",
  "rgb(16,185,129)",
  "rgb(244,63,94)",
];

function BranchingNode({ id, data, type }: NodeProps<NodeData>) {
  const isDecisionChoice =
    type === "decision" && (data as Record<string, unknown>)?.mode === "choice";
  const branches: BranchSpec[] = isDecisionChoice
    ? (((data as Record<string, unknown>).options as { label: string }[]) ?? []).map(
        (opt, idx) => ({
          id: opt.label,
          label: opt.label,
          color:
            CHOICE_BRANCH_COLORS[idx % CHOICE_BRANCH_COLORS.length] ??
            CHOICE_BRANCH_COLORS[0]!,
        })
      )
    : BRANCH_SPECS[type ?? ""] ?? [];
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <NodeChip type={type ?? "node"} id={id} data={data} />
      {branches.map((b, idx) => {
        // Distribute handles vertically along the right edge — for two
        // branches that's 33% and 67%, leaving room for the label tag.
        const topPercent = ((idx + 1) * 100) / (branches.length + 1);
        return (
          <div
            key={b.id}
            style={{ position: "absolute", right: -4, top: `${topPercent}%` }}
          >
            <Handle
              id={b.id}
              type="source"
              position={Position.Right}
              style={{
                ...HANDLE_STYLE,
                background: b.color,
                position: "relative",
                top: 0,
                right: 0,
              }}
            />
            <span
              className="pointer-events-none absolute select-none rounded-sm bg-white/95 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-foreground shadow-sm"
              style={{
                left: 18,
                top: -2,
                color: b.color,
                border: `1px solid ${b.color}`,
              }}
            >
              {b.label}
            </span>
          </div>
        );
      })}
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
  "if-else": BranchingNode,
  decision: BranchingNode,
  while: BranchingNode,
  "user-approval": BranchingNode,
  "join-chunks": InnerNode,
  note: InnerNode,
  guardrails: InnerNode,
  "gamma-ai": InnerNode,
  email: InnerNode,
  arcade: InnerNode,
  "vector-db": InnerNode,
  jira: InnerNode,
  confluence: InnerNode,
  "file-trigger": InnerNode,
  "file-write": InnerNode,
  "download-pdf": InnerNode,
  join: InnerNode,
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
  /** The workflow's own id -- threaded down to node panels so features
   *  like the transform "Test expression" section can fetch this
   *  workflow's own execution history. */
  workflowId?: string;
}

export function WorkflowCanvas({
  initialNodes,
  initialEdges,
  onNodesChange,
  onEdgesChange,
  runState,
  workflowId,
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
  // When the source is a branching node (if-else / while / user-approval)
  // the sourceHandle carries the branch label; we mirror it onto the
  // edge's `label` so the canvas reads naturally and `fromReactFlow`
  // can serialize `branch` for the backend without extra plumbing.
  const handleConnect = useCallback(
    (params: Connection) => {
      const sourceNode = nodes.find((n) => n.id === params.source);
      const isBranching =
        sourceNode &&
        (sourceNode.type === "if-else" ||
          sourceNode.type === "while" ||
          sourceNode.type === "user-approval");
      const branchLabel =
        isBranching && typeof params.sourceHandle === "string"
          ? params.sourceHandle
          : undefined;
      setEdges((prev) =>
        addEdge(
          {
            ...params,
            id: `edge-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            label: branchLabel,
          },
          prev
        )
      );
    },
    [nodes, setEdges]
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
          <Background gap={18} size={1.2} color="#d7d6de" />
          <Controls className="!shadow-md" />
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
          workflowId={workflowId}
        />
      )}
    </div>
  );
}
