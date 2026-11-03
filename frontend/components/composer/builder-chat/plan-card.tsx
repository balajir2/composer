"use client";

import { Button } from "@/components/ui/button";

export function PlanCard({
  stages,
  onApprove,
}: {
  stages: Array<{ id: string; summary: string }>;
  onApprove: () => void;
}) {
  return (
    <div className="rounded-md border p-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Proposed plan
      </p>
      <ol className="mb-3 list-decimal space-y-1 pl-5 text-sm">
        {stages.map((s) => (
          <li key={s.id}>{s.summary}</li>
        ))}
      </ol>
      <Button size="sm" onClick={onApprove}>
        Approve plan
      </Button>
    </div>
  );
}
