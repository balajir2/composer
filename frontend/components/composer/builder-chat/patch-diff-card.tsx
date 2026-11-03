"use client";

import { Button } from "@/components/ui/button";

export function PatchDiffCard({
  explanation,
  onApprove,
  onReject,
}: {
  explanation: string;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="rounded-md border p-3 text-sm">
      <p className="mb-2 font-medium">Suggested fix</p>
      <p className="mb-3 text-muted-foreground">{explanation}</p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onApprove}>
          Approve
        </Button>
        <Button size="sm" variant="outline" onClick={onReject}>
          Reject
        </Button>
      </div>
    </div>
  );
}
