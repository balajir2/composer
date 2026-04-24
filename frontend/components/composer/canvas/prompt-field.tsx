"use client";

import { useRef } from "react";
import type { Node as RFNode } from "reactflow";

import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { VariableReferencePicker } from "./variable-reference-picker";

type AnyNode = RFNode<Record<string, unknown>>;

/**
 * Textarea that plays nicely with the VariableReferencePicker:
 *   - Click a variable in the picker → splices `{{path}}` at the cursor
 *     (or at the end if the textarea isn't focused).
 *   - Drag a variable from the picker onto the textarea → splices at the
 *     drop position.
 *
 * Use for any prompt / template field that supports `{{var}}` substitution.
 */
export function PromptField({
  label,
  value,
  onChange,
  nodes,
  currentNodeId,
  rows = 4,
  placeholder,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  nodes: AnyNode[];
  currentNodeId: string;
  rows?: number;
  placeholder?: string;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  function insertAtCursor(snippet: string) {
    const el = ref.current;
    if (!el) {
      onChange(value + snippet);
      return;
    }
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? value.length;
    const next = value.slice(0, start) + snippet + value.slice(end);
    onChange(next);
    // Restore selection after React updates the value on next tick.
    requestAnimationFrame(() => {
      if (!ref.current) return;
      const caret = start + snippet.length;
      ref.current.focus();
      ref.current.setSelectionRange(caret, caret);
    });
  }

  function handleDrop(e: React.DragEvent<HTMLTextAreaElement>) {
    const data = e.dataTransfer.getData("text/plain");
    if (!data) return;
    e.preventDefault();
    const el = e.currentTarget;
    // Attempt to compute the drop position; if unsupported, fall back to
    // end-of-current-selection (browsers vary here, so we're permissive).
    const pos = el.selectionStart ?? value.length;
    const next = value.slice(0, pos) + data + value.slice(pos);
    onChange(next);
    requestAnimationFrame(() => {
      el.focus();
      const caret = pos + data.length;
      el.setSelectionRange(caret, caret);
    });
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label>{label}</Label>
        <VariableReferencePicker
          nodes={nodes}
          currentNodeId={currentNodeId}
          onSelect={(ref) => insertAtCursor(`{{${ref}}}`)}
        />
      </div>
      <Textarea
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        placeholder={placeholder}
        disabled={disabled}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes("text/plain")) {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
          }
        }}
        onDrop={handleDrop}
      />
    </div>
  );
}
