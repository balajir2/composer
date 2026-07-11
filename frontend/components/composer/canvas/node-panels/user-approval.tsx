"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export default function UserApprovalPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Message</Label>
        <Textarea
          value={(data.message as string) ?? ""}
          onChange={(e) => onChange({ message: e.target.value })}
          rows={3}
          placeholder="Please review and approve before continuing…"
        />
      </div>

      <div className="space-y-2">
        <Label>Approver email (optional)</Label>
        <Input
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
        <Label>CC (optional)</Label>
        <Input
          type="email"
          value={(data.approverCc as string) ?? ""}
          onChange={(e) => onChange({ approverCc: e.target.value })}
          placeholder="manager@example.com"
        />
      </div>
    </div>
  );
}
