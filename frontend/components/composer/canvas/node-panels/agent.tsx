"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useQuery } from "@tanstack/react-query";
import { getCatalog } from "@/lib/api/catalog";

export default function AgentPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const { data: catalog = [] } = useQuery({
    queryKey: ["catalog"],
    queryFn: getCatalog,
  });

  const selectedTools = Array.isArray(data.tools) ? (data.tools as string[]) : [];

  function toggleTool(toolId: string) {
    const next = selectedTools.includes(toolId)
      ? selectedTools.filter((t) => t !== toolId)
      : [...selectedTools, toolId];
    onChange({ tools: next });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Provider</Label>
        <Select
          value={(data.provider as string) ?? ""}
          onValueChange={(v) => onChange({ provider: v })}
        >
          <SelectTrigger>
            <SelectValue placeholder="Select provider" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="anthropic">Anthropic</SelectItem>
            <SelectItem value="openai">OpenAI</SelectItem>
            <SelectItem value="google">Google</SelectItem>
            <SelectItem value="groq">Groq</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>Model</Label>
        <Input
          value={(data.model as string) ?? ""}
          onChange={(e) => onChange({ model: e.target.value })}
          placeholder="claude-sonnet-4-6"
        />
      </div>

      <div className="space-y-2">
        <Label>System prompt</Label>
        <Textarea
          value={(data.systemPrompt as string) ?? ""}
          onChange={(e) => onChange({ systemPrompt: e.target.value })}
          rows={4}
          placeholder="You are a helpful assistant…"
        />
      </div>

      {catalog.length > 0 && (
        <div className="space-y-2">
          <Label>Tools</Label>
          <div className="space-y-1 rounded-md border p-2">
            {catalog.map((tool) => {
              const id = tool.id;
              const label = tool.kind === "builtin" ? tool.label : tool.name;
              return (
                <label key={id} className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selectedTools.includes(id)}
                    onChange={() => toggleTool(id)}
                    className="h-3.5 w-3.5"
                  />
                  {label}
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
