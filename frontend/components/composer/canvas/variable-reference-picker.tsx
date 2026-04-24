"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Node as RFNode } from "reactflow";
import { Braces } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type AnyNode = RFNode<Record<string, unknown>>;

type PickerItem = {
  label: string;
  path: string;
  description?: string;
  kind?: string;
  isField?: boolean;
};

type Group = { category: string; items: PickerItem[] };

type InputVar = { name?: unknown; description?: unknown; type?: unknown };

/**
 * Port of OAB's VariableReferencePicker adapted to Composer.  Lists variables
 * available to the node editing right now:
 *   - Input Variables  — declared on the Start node's inputVariables.
 *   - Workflow         — `input` (full caller payload) + `lastOutput`.
 *   - Previous Nodes   — per-node base reference, plus individual properties
 *                        if the node declares a jsonOutputSchema.
 *
 * The dropdown is rendered through a portal with `fixed` positioning so it
 * can't be clipped by ancestor `overflow` containers (e.g. the property-
 * panel sidebar).
 */
export function VariableReferencePicker({
  nodes,
  currentNodeId,
  onSelect,
  buttonLabel = "Insert variable",
}: {
  nodes: AnyNode[];
  currentNodeId: string;
  onSelect: (reference: string) => void;
  buttonLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; right: number } | null>(
    null
  );
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  function updateAnchor() {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    setAnchor({ top: rect.bottom + 6, right: window.innerWidth - rect.right });
  }

  function openPopover() {
    updateAnchor();
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent) {
      const t = e.target as Node;
      if (
        buttonRef.current?.contains(t) ||
        popoverRef.current?.contains(t)
      ) {
        return;
      }
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onReflow() {
      updateAnchor();
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onReflow, true);
    window.addEventListener("resize", onReflow);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onReflow, true);
      window.removeEventListener("resize", onReflow);
    };
  }, [open]);

  const groups = useMemo<Group[]>(() => {
    const others = nodes.filter((n) => n.id !== currentNodeId);
    const startNode = nodes.find((n) => n.type === "start");

    const rawInputs =
      (startNode?.data?.inputVariables as InputVar[] | undefined) ??
      (startNode?.data?.inputs as InputVar[] | undefined) ??
      [];
    const inputVars: PickerItem[] = rawInputs
      .filter((v) => typeof v.name === "string" && v.name)
      .map((v) => ({
        label: String(v.name),
        path: String(v.name),
        description:
          (typeof v.description === "string" && v.description) ||
          `Input: ${String(v.name)}`,
        kind: typeof v.type === "string" ? v.type : undefined,
      }));

    const previousNodes: PickerItem[] = others.flatMap((n) => {
      const d = (n.data ?? {}) as Record<string, unknown>;
      if (n.type === "start") return [];
      const rawNodeName = typeof d.nodeName === "string" ? d.nodeName : "";
      const display =
        rawNodeName ||
        (typeof d.label === "string" && d.label) ||
        n.id;
      // Prefer the user-given Name (snake-cased) as the variable path —
      // reads much better than `agent_1` and matches the backend alias
      // registered in events_wrapper.py.  Falls back to sanitized ID.
      const nameAlias = rawNodeName
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "");
      const varName = nameAlias || n.id.replace(/-/g, "_");
      const base: PickerItem = {
        label: String(display),
        path: varName,
        description: `Output from ${display}`,
        kind: n.type ?? undefined,
      };
      const vars: PickerItem[] = [base];

      // Backend canonical: `jsonSchema` (Pydantic alias on AgentNodeData).
      // Also accept the legacy `jsonOutputSchema` key from older canvases
      // and both string- and object-shaped schemas.
      const schemaRaw: unknown =
        (d.jsonSchema as unknown) ?? (d.jsonOutputSchema as unknown);
      let schema: { properties?: Record<string, { type?: string; description?: string }> } | null =
        null;
      if (typeof schemaRaw === "string" && schemaRaw.trim()) {
        try {
          schema = JSON.parse(schemaRaw);
        } catch {
          /* invalid JSON — skip per-field expansion */
        }
      } else if (schemaRaw && typeof schemaRaw === "object") {
        schema = schemaRaw as unknown as typeof schema;
      }
      if (schema?.properties) {
        for (const [propName, propSchema] of Object.entries(schema.properties)) {
          vars.push({
            label: `${display}.${propName}`,
            path: `${varName}.${propName}`,
            description:
              propSchema.description ?? `${propName} from ${display}`,
            kind: propSchema.type ?? "any",
            isField: true,
          });
        }
      }
      return vars;
    });

    const out: Group[] = [
      { category: "Input variables", items: inputVars },
      {
        category: "Workflow",
        items: [
          {
            label: "input",
            path: "input",
            description: "Full caller payload as object.",
          },
          {
            label: "lastOutput",
            path: "lastOutput",
            description: "Output from the immediately-previous node.",
          },
        ],
      },
      { category: "Previous nodes", items: previousNodes },
    ];
    return out.filter((g) => g.items.length > 0);
  }, [nodes, currentNodeId]);

  function handlePick(path: string) {
    onSelect(path);
    setOpen(false);
  }

  const popover =
    open && anchor && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={popoverRef}
            role="dialog"
            aria-label="Available variables"
            className="fixed z-[60] w-80 max-w-[calc(100vw-16px)] rounded-md border bg-popover text-popover-foreground shadow-xl"
            style={{ top: anchor.top, right: anchor.right }}
          >
            <div className="border-b px-3 py-2">
              <p className="text-xs font-semibold">Available variables</p>
              <p className="text-xs text-muted-foreground">
                Click or drag to insert{" "}
                <code className="font-mono">{"{{name}}"}</code>
              </p>
            </div>
            <div className="max-h-80 overflow-y-auto">
              {groups.map((group) => (
                <div key={group.category}>
                  <div className="sticky top-0 bg-muted/80 px-3 py-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    {group.category}
                  </div>
                  {group.items.map((item) => (
                    <button
                      key={`${group.category}-${item.path}`}
                      type="button"
                      onClick={() => handlePick(item.path)}
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.setData("text/plain", `{{${item.path}}}`);
                        e.dataTransfer.effectAllowed = "copy";
                      }}
                      className={`flex w-full items-start gap-2 border-b px-3 py-2 text-left text-xs transition-colors last:border-0 hover:bg-muted ${
                        item.isField ? "bg-muted/30 pl-6" : ""
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1">
                          {item.isField && (
                            <span className="text-muted-foreground">↳</span>
                          )}
                          <span className="truncate font-medium">{item.label}</span>
                        </div>
                        <div className="truncate font-mono text-[10px] text-muted-foreground">
                          {`{{${item.path}}}`}
                        </div>
                        {item.description && (
                          <div className="truncate text-[11px] text-muted-foreground">
                            {item.description}
                          </div>
                        )}
                      </div>
                      {item.kind && (
                        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          {item.kind}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>,
          document.body
        )
      : null;

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => (open ? setOpen(false) : openPopover())}
        title="Insert a reference to an upstream variable"
        className={cn(
          buttonVariants({ variant: "outline", size: "sm" }),
          "h-7 px-2 text-xs"
        )}
      >
        <Braces className="mr-1 h-3.5 w-3.5" />
        {buttonLabel}
      </button>
      {popover}
    </>
  );
}
