"use client";

import { CheckCircle2, XCircle, X } from "lucide-react";

export type InlineTestState =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string; status?: number | null };

export function InlineTestResult({
  state,
  onDismiss,
}: {
  state: InlineTestState;
  onDismiss: () => void;
}) {
  if (state.kind === "idle" || state.kind === "pending") return null;

  const isErr = state.kind === "error";
  const Icon = isErr ? XCircle : CheckCircle2;
  const color = isErr
    ? "border-destructive/40 bg-destructive/10 text-destructive"
    : "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";

  return (
    <div
      className={`mt-1 flex items-start gap-2 rounded-md border px-2 py-1 text-xs ${color}`}
      role="status"
    >
      <Icon className="mt-0.5 size-3.5 shrink-0" />
      <div className="min-w-0 flex-1 break-words">
        {isErr && state.status != null && (
          <span className="mr-1 font-mono font-semibold">HTTP {state.status}</span>
        )}
        {state.message}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        className="shrink-0 rounded p-0.5 hover:bg-current/10"
      >
        <X className="size-3" />
      </button>
    </div>
  );
}
