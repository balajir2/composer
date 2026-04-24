"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";

const METHOD_OPTIONS = [
  { value: "GET", label: "GET" },
  { value: "POST", label: "POST" },
  { value: "PUT", label: "PUT" },
  { value: "PATCH", label: "PATCH" },
  { value: "DELETE", label: "DELETE" },
];

export default function HttpPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="http-method">Method</Label>
        <NativeSelect
          id="http-method"
          value={(data.method as string) ?? "GET"}
          onValueChange={(v) => onChange({ method: v })}
          options={METHOD_OPTIONS}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="http-url">URL</Label>
        <Input
          id="http-url"
          value={(data.url as string) ?? ""}
          onChange={(e) => onChange({ url: e.target.value })}
          placeholder="https://api.example.com/endpoint"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="http-headers">Headers (JSON)</Label>
        <Textarea
          id="http-headers"
          value={(data.headers as string) ?? ""}
          onChange={(e) => onChange({ headers: e.target.value })}
          rows={3}
          placeholder='{"Authorization": "Bearer {{token}}"}'
          className="font-mono text-xs"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="http-body">Body (JSON)</Label>
        <Textarea
          id="http-body"
          value={(data.body as string) ?? ""}
          onChange={(e) => onChange({ body: e.target.value })}
          rows={4}
          placeholder='{"key": "{{value}}"}'
          className="font-mono text-xs"
        />
      </div>
    </div>
  );
}
