"use client";

import type { Node as RFNode } from "reactflow";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import StartPanel from "./node-panels/start";
import EndPanel from "./node-panels/end";
import AgentPanel from "./node-panels/agent";
import McpPanel from "./node-panels/mcp";
import HttpPanel from "./node-panels/http";
import SetStatePanel from "./node-panels/set-state";
import TransformPanel from "./node-panels/transform";
import DataTransformPanel from "./node-panels/data-transform";
import ExtractPanel from "./node-panels/extract";
import IfElsePanel from "./node-panels/if-else";
import WhilePanel from "./node-panels/while";
import UserApprovalPanel from "./node-panels/user-approval";
import JoinChunksPanel from "./node-panels/join-chunks";
import NotePanel from "./node-panels/note";
import GuardrailsPanel from "./node-panels/guardrails";
import GammaAiPanel from "./node-panels/gamma-ai";
import ArcadePanel from "./node-panels/arcade";
import VectorDbPanel from "./node-panels/vector-db";

// ---------------------------------------------------------------------------
// Panel component type
// ---------------------------------------------------------------------------
type PanelProps = {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
};

type PanelComponent = (props: PanelProps) => React.ReactElement | null;

const PANEL_MAP: Record<string, PanelComponent> = {
  start: StartPanel,
  end: EndPanel,
  agent: AgentPanel,
  mcp: McpPanel,
  http: HttpPanel,
  "set-state": SetStatePanel,
  transform: TransformPanel,
  "data-transform": DataTransformPanel,
  extract: ExtractPanel,
  "if-else": IfElsePanel,
  while: WhilePanel,
  "user-approval": UserApprovalPanel,
  "join-chunks": JoinChunksPanel,
  note: NotePanel,
  guardrails: GuardrailsPanel,
  "gamma-ai": GammaAiPanel,
  arcade: ArcadePanel,
  "vector-db": VectorDbPanel,
};

const TYPE_LABELS: Record<string, string> = {
  start: "Start",
  end: "End",
  agent: "Agent",
  mcp: "MCP",
  http: "HTTP",
  "set-state": "Set State",
  transform: "Transform",
  "data-transform": "Data Transform",
  extract: "Extract",
  "if-else": "If / Else",
  while: "While",
  "user-approval": "User Approval",
  "join-chunks": "Join Chunks",
  note: "Note",
  guardrails: "Guardrails",
  "gamma-ai": "Gamma AI",
  arcade: "Arcade",
  "vector-db": "Vector DB",
};

// ---------------------------------------------------------------------------
// PropertyPanel
// ---------------------------------------------------------------------------
interface PropertyPanelProps {
  node: RFNode;
  onChange: (patch: Record<string, unknown>) => void;
  onClose: () => void;
}

export function PropertyPanel({ node, onChange, onClose }: PropertyPanelProps) {
  const PanelContent = PANEL_MAP[node.type ?? ""];
  const title = TYPE_LABELS[node.type ?? ""] ?? node.type ?? "Node";
  const nodeData = (node.data ?? {}) as Record<string, unknown>;

  return (
    <div className="flex h-full w-72 flex-col overflow-y-auto border-l bg-background">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
            Properties
          </p>
          <p className="text-sm font-medium">{title}</p>
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Panel body */}
      <div className="flex-1 overflow-y-auto p-4">
        {PanelContent ? (
          <PanelContent data={nodeData} onChange={onChange} />
        ) : (
          <p className="text-muted-foreground text-sm">
            No properties available for this node type.
          </p>
        )}
      </div>
    </div>
  );
}
