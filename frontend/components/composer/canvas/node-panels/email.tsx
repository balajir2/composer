"use client";

import type { Node as RFNode } from "reactflow";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { PromptField } from "../prompt-field";

const BODY_TYPE_OPTIONS = [
  { value: "html", label: "HTML" },
  { value: "text", label: "Plain text" },
];

export default function EmailPanel({
  data,
  onChange,
  allNodes,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  allNodes?: RFNode[];
  currentNodeId?: string;
}) {
  const bodyType = (data.emailBodyType as string) ?? "html";

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="email-provider">Provider</Label>
        <NativeSelect
          id="email-provider"
          value={(data.emailProvider as string) ?? "resend"}
          onValueChange={(v) => onChange({ emailProvider: v })}
          options={[{ value: "resend", label: "Resend" }]}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="email-from">From</Label>
        <Input
          id="email-from"
          value={(data.emailFrom as string) ?? ""}
          onChange={(e) => onChange({ emailFrom: e.target.value })}
          placeholder="Reports <reports@example.com>"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="email-to">To</Label>
        <Input
          id="email-to"
          value={(data.emailTo as string) ?? ""}
          onChange={(e) => onChange({ emailTo: e.target.value })}
          placeholder="{{recipient_email}} or team@example.com"
        />
        <p className="text-xs text-muted-foreground">
          Separate multiple addresses with commas, semicolons, or new lines.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="email-cc">CC</Label>
          <Input
            id="email-cc"
            value={(data.emailCc as string) ?? ""}
            onChange={(e) => onChange({ emailCc: e.target.value || undefined })}
            placeholder="optional"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="email-bcc">BCC</Label>
          <Input
            id="email-bcc"
            value={(data.emailBcc as string) ?? ""}
            onChange={(e) => onChange({ emailBcc: e.target.value || undefined })}
            placeholder="optional"
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="email-reply-to">Reply-To</Label>
        <Input
          id="email-reply-to"
          value={(data.emailReplyTo as string) ?? ""}
          onChange={(e) => onChange({ emailReplyTo: e.target.value || undefined })}
          placeholder="optional"
        />
      </div>

      <PromptField
        label="Subject"
        value={(data.emailSubject as string) ?? ""}
        onChange={(v) => onChange({ emailSubject: v })}
        nodes={allNodes ?? []}
        currentNodeId={currentNodeId ?? ""}
        rows={2}
        placeholder="Your workflow result for {{customer_name}}"
      />

      <div className="space-y-2">
        <Label htmlFor="email-body-type">Body type</Label>
        <NativeSelect
          id="email-body-type"
          value={bodyType}
          onValueChange={(v) => onChange({ emailBodyType: v })}
          options={BODY_TYPE_OPTIONS}
        />
      </div>

      <PromptField
        label="Body"
        value={(data.emailBody as string) ?? ""}
        onChange={(v) => onChange({ emailBody: v })}
        nodes={allNodes ?? []}
        currentNodeId={currentNodeId ?? ""}
        rows={10}
        placeholder={
          bodyType === "html"
            ? "<h1>Report</h1><p>{{lastOutput}}</p>"
            : "Report\n\n{{lastOutput}}"
        }
      />

      <div className="space-y-2">
        <Label htmlFor="email-idempotency-key">
          Idempotency key{" "}
          <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Input
          id="email-idempotency-key"
          value={(data.emailIdempotencyKey as string) ?? ""}
          onChange={(e) =>
            onChange({ emailIdempotencyKey: e.target.value || undefined })
          }
          placeholder="workflow-{{execution_id}}"
        />
        <p className="text-xs text-muted-foreground">
          Resend uses this to avoid duplicate sends for repeated requests.
        </p>
      </div>
    </div>
  );
}
