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
        <Label>Approver email</Label>
        <Input
          type="email"
          value={(data.approverEmail as string) ?? ""}
          onChange={(e) => onChange({ approverEmail: e.target.value })}
          placeholder="approver@example.com"
        />
      </div>
    </div>
  );
}
