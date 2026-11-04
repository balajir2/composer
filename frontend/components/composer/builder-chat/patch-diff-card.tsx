"use client";

import { Button } from "@/components/ui/button";

export function PatchDiffCard({
  explanation,
  onApprove,
  onReject,
  decided = false,
  disabled = false,
}: {
  explanation: string;
  onApprove: () => void;
  onReject: () => void;
  decided?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="rounded-xl border bg-card p-3 text-sm shadow-sm">
      <p className="mb-2 font-medium">Suggested fix</p>
      <p className="mb-3 whitespace-pre-wrap text-muted-foreground">{explanation}</p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onApprove} disabled={decided || disabled}>
          Approve
        </Button>
        <Button size="sm" variant="outline" onClick={onReject} disabled={decided || disabled}>
          Reject
        </Button>
      </div>
    </div>
  );
}
