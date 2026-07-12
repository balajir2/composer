"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, Wrench, Server } from "lucide-react";
import { Input } from "@/components/ui/input";
import { getCatalog, type CatalogTool } from "@/lib/api/catalog";
import { visualFor } from "./node-visuals";

// ---------------------------------------------------------------------------
// Drag data written to dataTransfer on each palette item.
// The canvas drop handler reads this to create a new node.
// ---------------------------------------------------------------------------
export type PaletteDragData =
  | { kind: "builtin"; id: string; label: string }
  | { kind: "mcp"; id: string; name: string; url: string }
  | { kind: "node"; nodeType: string; label: string };

export const COMPOSER_NODE_PALETTE: { nodeType: string; label: string }[] = [
  { nodeType: "start", label: "Start" },
  { nodeType: "end", label: "End" },
  { nodeType: "agent", label: "Agent" },
  { nodeType: "mcp", label: "MCP" },
  { nodeType: "http", label: "HTTP" },
  { nodeType: "set-state", label: "Set State" },
  { nodeType: "transform", label: "Transform" },
  { nodeType: "data-transform", label: "Data Transform" },
  { nodeType: "extract", label: "Extract" },
  { nodeType: "if-else", label: "If / Else" },
  { nodeType: "while", label: "While" },
  { nodeType: "user-approval", label: "User Approval" },
  { nodeType: "join-chunks", label: "Join Chunks" },
  { nodeType: "note", label: "Note" },
  { nodeType: "guardrails", label: "Guardrails" },
  { nodeType: "gamma-ai", label: "Gamma AI" },
  { nodeType: "email", label: "Email" },
  { nodeType: "arcade", label: "Arcade" },
  { nodeType: "vector-db", label: "Vector DB" },
  { nodeType: "file-trigger", label: "File Trigger" },
  { nodeType: "file-write", label: "File Write" },
  { nodeType: "jira", label: "Jira" },
];

// ---------------------------------------------------------------------------
// PaletteItem
// ---------------------------------------------------------------------------
function PaletteItem({
  dragData,
  label,
  icon,
}: {
  dragData: PaletteDragData;
  label: string;
  icon?: React.ReactNode;
}) {
  function handleDragStart(e: React.DragEvent<HTMLDivElement>) {
    e.dataTransfer.setData("application/composer-palette", JSON.stringify(dragData));
    e.dataTransfer.effectAllowed = "copy";
  }

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      className="flex cursor-grab items-center gap-2 rounded-md border bg-card px-2 py-1.5 text-sm shadow-sm hover:bg-accent active:cursor-grabbing"
    >
      {icon}
      <span className="truncate">{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ToolsPalette
// ---------------------------------------------------------------------------
export function ToolsPalette() {
  const [search, setSearch] = useState("");

  const { data: catalog = [], isLoading } = useQuery({
    queryKey: ["catalog"],
    queryFn: getCatalog,
  });

  const q = search.toLowerCase();

  const filteredNodes = COMPOSER_NODE_PALETTE.filter((n) => n.label.toLowerCase().includes(q));

  const filteredCatalog = catalog.filter((t) => {
    const name = t.kind === "builtin" ? t.label : t.name;
    return name.toLowerCase().includes(q);
  });

  const builtins = filteredCatalog.filter(
    (t): t is Extract<CatalogTool, { kind: "builtin" }> => t.kind === "builtin"
  );
  const mcps = filteredCatalog.filter(
    (t): t is Extract<CatalogTool, { kind: "mcp" }> => t.kind === "mcp"
  );

  return (
    <div className="flex h-full w-52 flex-col gap-3 overflow-y-auto border-r bg-background p-3">
      {/* Search */}
      <div className="relative">
        <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter…"
          className="h-7 pl-7 text-xs"
        />
      </div>

      {/* Node types */}
      {filteredNodes.length > 0 && (
        <section>
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Nodes
          </p>
          <div className="flex flex-col gap-1">
            {filteredNodes.map((n) => {
              const visual = visualFor(n.nodeType);
              const Icon = visual.icon;
              return (
                <PaletteItem
                  key={n.nodeType}
                  dragData={{ kind: "node", nodeType: n.nodeType, label: n.label }}
                  label={n.label}
                  icon={
                    <span
                      className={`flex size-5 shrink-0 items-center justify-center rounded ${visual.iconWrapClass}`}
                    >
                      <Icon className="size-3" />
                    </span>
                  }
                />
              );
            })}
          </div>
        </section>
      )}

      {/* Built-in tools */}
      {builtins.length > 0 && (
        <section>
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Built-in Tools
          </p>
          <div className="flex flex-col gap-1">
            {builtins.map((t) => (
              <PaletteItem
                key={t.id}
                dragData={{ kind: "builtin", id: t.id, label: t.label }}
                label={t.label}
                icon={<Wrench className="h-3.5 w-3.5 shrink-0 text-blue-500" />}
              />
            ))}
          </div>
        </section>
      )}

      {/* Shared MCPs */}
      {mcps.length > 0 && (
        <section>
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            MCP Servers
          </p>
          <div className="flex flex-col gap-1">
            {mcps.map((t) => (
              <PaletteItem
                key={t.id}
                dragData={{ kind: "mcp", id: t.id, name: t.name, url: t.url }}
                label={t.name}
                icon={<Server className="h-3.5 w-3.5 shrink-0 text-purple-500" />}
              />
            ))}
          </div>
        </section>
      )}

      {isLoading && <p className="text-center text-xs text-muted-foreground">Loading catalog…</p>}

      {!isLoading && filteredNodes.length === 0 && filteredCatalog.length === 0 && (
        <p className="text-center text-xs text-muted-foreground">No matches</p>
      )}
    </div>
  );
}
