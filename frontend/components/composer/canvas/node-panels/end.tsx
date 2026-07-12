"use client";

export default function EndPanel(_props: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  // outputRenderHint was removed (P0-0): EndNodeData has no such field and
  // EndExecutor never read it, so it was a silent no-op control that made
  // the node look configurable when nothing it set had any effect. If
  // execution-result render hints are wanted, that's a separate feature
  // requiring a backend field + API + result-view change, not a re-add
  // of the dead frontend-only control.
  return (
    <p className="text-sm text-muted-foreground">
      End has no configurable fields — it surfaces{" "}
      <code className="font-mono">variables.lastOutput</code> as the workflow&apos;s final
      output.
    </p>
  );
}
