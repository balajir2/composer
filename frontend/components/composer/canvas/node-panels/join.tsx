"use client";

export default function JoinPanel(_props: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  return (
    <p className="text-sm text-muted-foreground">
      Join has no configurable fields — it merges multiple incoming branches into a single
      path forward. Connect it to exactly one downstream node.
    </p>
  );
}
