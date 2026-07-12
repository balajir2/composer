"use client";

import type { Node as RFNode } from "reactflow";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import EmailPanel from "./node-panels/email";
import ArcadePanel from "./node-panels/arcade";
import VectorDbPanel from "./node-panels/vector-db";
import JiraPanel from "./node-panels/jira";
import FileTriggerPanel from "./node-panels/file-trigger";

// ---------------------------------------------------------------------------
// Panel component type
// ---------------------------------------------------------------------------
export type PanelProps = {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  /** All workflow nodes — panels use this to render the variable picker
   *  for prompts, template fields, etc.  Optional so panels that don't
   *  need it (e.g. Note) can ignore it. */
  allNodes?: RFNode[];
  /** The id of the node currently being edited (so the picker can exclude
   *  it from "Previous nodes"). */
  currentNodeId?: string;
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
  email: EmailPanel,
  arcade: ArcadePanel,
  "vector-db": VectorDbPanel,
  jira: JiraPanel,
  "file-trigger": FileTriggerPanel,
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
  email: "Email",
  arcade: "Arcade",
  "vector-db": "Vector DB",
  jira: "Jira",
  "file-trigger": "File Trigger",
};

// ---------------------------------------------------------------------------
// PropertyPanel
// ---------------------------------------------------------------------------
interface PropertyPanelProps {
  node: RFNode;
  /** Every node on the canvas — used by the variable picker. */
  allNodes: RFNode[];
  onChange: (patch: Record<string, unknown>) => void;
  onClose: () => void;
}

// Snake-case the user's node name so the variable-substitution path
// (`{{place_extractor.city}}`) resolves cleanly.  Dots are reserved as
// path separators and hyphens work but read worse than underscores.
function sanitizeVarAlias(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export function PropertyPanel({ node, allNodes, onChange, onClose }: PropertyPanelProps) {
  const PanelContent = PANEL_MAP[node.type ?? ""];
  const typeLabel = TYPE_LABELS[node.type ?? ""] ?? node.type ?? "Node";
  const nodeData = (node.data ?? {}) as Record<string, unknown>;
  const nodeName = (nodeData.nodeName as string | undefined) ?? "";
  const varAlias = nodeName ? sanitizeVarAlias(nodeName) : node.id.replace(/-/g, "_");

  return (
    <div className="flex h-full w-72 flex-col overflow-y-auto border-l bg-background">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {typeLabel}
          </p>
          <p className="truncate font-mono text-[10px] text-muted-foreground">
            {node.id}
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={onClose}
          aria-label="Close properties panel"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Shared "Name" field — applies to every node type.  Settles a
          readable variable-path alias (`{{<name>.field}}`) so prompts
          don't have to reference opaque node IDs. */}
      <div className="space-y-1.5 border-b px-4 py-3">
        <Label htmlFor="node-name" className="text-xs">
          Name
        </Label>
        <Input
          id="node-name"
          value={nodeName}
          onChange={(e) => onChange({ nodeName: e.target.value })}
          placeholder={`e.g. ${typeLabel.toLowerCase().replace(/\s+/g, "_")}`}
        />
        <p className="text-[10px] text-muted-foreground">
          Reference in prompts as{" "}
          <code className="font-mono">{`{{${varAlias}}}`}</code>
          {" "}or{" "}
          <code className="font-mono">{`{{${varAlias}.<field>}}`}</code>{" "}
          if this node returns JSON.
        </p>
      </div>

      {/* Panel body */}
      <div className="flex-1 overflow-y-auto p-4">
        {PanelContent ? (
          <PanelContent
            data={nodeData}
            onChange={onChange}
            allNodes={allNodes}
            currentNodeId={node.id}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            No properties available for this node type.
          </p>
        )}
      </div>
    </div>
  );
}
