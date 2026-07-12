"use client";

import { useEffect } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export default function UserApprovalPanel({
  data,
  onChange,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  // Legacy-field migration (P0-0): the Designer used to write `message`
  // instead of the canonical `approvalMessage` alias UserApprovalNodeData
  // actually reads.
  useEffect(() => {
    if (data.approvalMessage === undefined && typeof data.message === "string") {
      onChange({ approvalMessage: data.message, message: undefined });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNodeId]);

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="user-approval-message">Message</Label>
        <Textarea
          id="user-approval-message"
          value={(data.approvalMessage as string) ?? ""}
          onChange={(e) => onChange({ approvalMessage: e.target.value })}
          rows={3}
          placeholder="Please review and approve before continuing…"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="user-approval-email">Approver email (optional)</Label>
        <Input
          id="user-approval-email"
          type="email"
          value={(data.approverEmail as string) ?? ""}
          onChange={(e) => onChange({ approverEmail: e.target.value })}
          placeholder="approver@example.com"
        />
        <p className="text-[10px] text-muted-foreground">
          If set, this person gets an email with one-click Approve/Reject links — no
          Composer login required. In-app approval always still works too.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="user-approval-cc">CC (optional)</Label>
        <Input
          id="user-approval-cc"
          type="email"
          value={(data.approverCc as string) ?? ""}
          onChange={(e) => onChange({ approverCc: e.target.value })}
          placeholder="manager@example.com"
        />
      </div>
    </div>
  );
}
