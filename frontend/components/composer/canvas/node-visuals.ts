/**
 * Per-node-type visual identity — icon + accent color + display label.
 *
 * The canvas pulls from this so every node type is instantly recognizable
 * (n8n / OAB-style chips) instead of looking like a uniform grey box.
 *
 * Adding a new node type:
 *   1. Add an entry below.
 *   2. Add the type to COMPOSER_NODE_TYPES in workflow-canvas.tsx.
 *   3. Add a panel under canvas/node-panels and register it in
 *      property-panel.tsx PANEL_MAP.
 */

import {
  Bot,
  CheckCircle2,
  Combine,
  Database,
  FileSearch,
  Mail,
  Gamepad2,
  GitBranch,
  Globe,
  HelpCircle,
  Layers,
  ListTodo,
  PlayCircle,
  Plug,
  Presentation,
  Repeat,
  Shield,
  Sparkles,
  StickyNote,
  StopCircle,
  Variable,
  type LucideIcon,
} from "lucide-react";

export type NodeVisual = {
  icon: LucideIcon;
  /** Tailwind utility classes for the icon background tile.  Pick a colour
   *  from a palette that reads on top of the page's white surface. */
  iconWrapClass: string;
  /** Tailwind class for the label colour beside the icon. */
  accent: string;
  /** Human-readable single-word label.  Used as the default node display
   *  when the user hasn't set a Name yet. */
  label: string;
};

const FALLBACK: NodeVisual = {
  icon: HelpCircle,
  iconWrapClass: "bg-slate-100 text-slate-600",
  accent: "text-slate-700",
  label: "Node",
};

export const NODE_VISUALS: Record<string, NodeVisual> = {
  start: {
    icon: PlayCircle,
    iconWrapClass: "bg-emerald-100 text-emerald-700",
    accent: "text-emerald-700",
    label: "Start",
  },
  end: {
    icon: StopCircle,
    iconWrapClass: "bg-rose-100 text-rose-700",
    accent: "text-rose-700",
    label: "End",
  },
  agent: {
    icon: Bot,
    // Bounteous brand-purple tile — agent is the workhorse, deserves the
    // primary palette.
    iconWrapClass: "bg-[var(--brand-pink-alpha)] text-[var(--brand-deep)]",
    accent: "text-[var(--brand-deep)]",
    label: "Agent",
  },
  mcp: {
    icon: Plug,
    iconWrapClass: "bg-teal-100 text-teal-700",
    accent: "text-teal-700",
    label: "MCP",
  },
  http: {
    icon: Globe,
    iconWrapClass: "bg-sky-100 text-sky-700",
    accent: "text-sky-700",
    label: "HTTP",
  },
  "set-state": {
    icon: Variable,
    iconWrapClass: "bg-amber-100 text-amber-700",
    accent: "text-amber-700",
    label: "Set state",
  },
  transform: {
    icon: Sparkles,
    iconWrapClass: "bg-violet-100 text-violet-700",
    accent: "text-violet-700",
    label: "Transform",
  },
  "data-transform": {
    icon: Layers,
    iconWrapClass: "bg-indigo-100 text-indigo-700",
    accent: "text-indigo-700",
    label: "Data transform",
  },
  extract: {
    icon: FileSearch,
    iconWrapClass: "bg-fuchsia-100 text-fuchsia-700",
    accent: "text-fuchsia-700",
    label: "Extract",
  },
  "if-else": {
    icon: GitBranch,
    iconWrapClass: "bg-orange-100 text-orange-700",
    accent: "text-orange-700",
    label: "If / Else",
  },
  while: {
    icon: Repeat,
    iconWrapClass: "bg-cyan-100 text-cyan-700",
    accent: "text-cyan-700",
    label: "While",
  },
  "user-approval": {
    icon: CheckCircle2,
    iconWrapClass: "bg-yellow-100 text-yellow-700",
    accent: "text-yellow-700",
    label: "User approval",
  },
  "join-chunks": {
    icon: Combine,
    iconWrapClass: "bg-pink-100 text-pink-700",
    accent: "text-pink-700",
    label: "Join chunks",
  },
  note: {
    icon: StickyNote,
    iconWrapClass: "bg-yellow-100 text-yellow-800",
    accent: "text-yellow-800",
    label: "Note",
  },
  guardrails: {
    icon: Shield,
    iconWrapClass: "bg-red-100 text-red-700",
    accent: "text-red-700",
    label: "Guardrails",
  },
  "gamma-ai": {
    icon: Presentation,
    iconWrapClass: "bg-orange-100 text-orange-700",
    accent: "text-orange-700",
    label: "Gamma AI",
  },
  email: {
    icon: Mail,
    iconWrapClass: "bg-cyan-100 text-cyan-700",
    accent: "text-cyan-700",
    label: "Email",
  },
  arcade: {
    icon: Gamepad2,
    iconWrapClass: "bg-blue-100 text-blue-700",
    accent: "text-blue-700",
    label: "Arcade",
  },
  "vector-db": {
    icon: Database,
    iconWrapClass: "bg-emerald-100 text-emerald-700",
    accent: "text-emerald-700",
    label: "Vector DB",
  },
  jira: {
    icon: ListTodo,
    iconWrapClass: "bg-blue-100 text-blue-700",
    accent: "text-blue-700",
    label: "Jira",
  },
};

export function visualFor(type: string | undefined): NodeVisual {
  if (!type) return FALLBACK;
  return NODE_VISUALS[type] ?? FALLBACK;
}
