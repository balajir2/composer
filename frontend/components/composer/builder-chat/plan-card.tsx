"use client";

import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";

export function PlanCard({
  stages,
  onApprove,
  approved = false,
  disabled = false,
}: {
  stages: Array<{ id: string; summary: string }>;
  onApprove: () => void;
  approved?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="rounded-xl border bg-card p-3 shadow-sm">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Proposed plan
      </p>
      <ol className="mb-3 space-y-2 text-sm">
        {stages.map((s, i) => (
          <li key={s.id} className="flex gap-2">
            <span className="bg-primary/10 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-primary">
              {i + 1}
            </span>
            <span>{s.summary}</span>
          </li>
        ))}
      </ol>
      <Button size="sm" onClick={onApprove} disabled={approved || disabled}>
        {approved ? (
          <>
            <Check className="mr-1 h-3.5 w-3.5" aria-hidden /> Approved
          </>
        ) : (
          "Approve plan"
        )}
      </Button>
    </div>
  );
}
