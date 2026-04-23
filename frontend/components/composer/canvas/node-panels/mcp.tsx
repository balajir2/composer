"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useQuery } from "@tanstack/react-query";
import { getCatalog } from "@/lib/api/catalog";

export default function McpPanel({
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

  const sharedMcps = catalog.filter((t) => t.kind === "mcp") as {
    kind: "mcp";
    id: string;
    name: string;
    url: string;
  }[];

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>MCP Server</Label>
        <Select
          value={(data.serverId as string) ?? ""}
          onValueChange={(v) => onChange({ serverId: v })}
        >
          <SelectTrigger>
            <SelectValue placeholder="Select server" />
          </SelectTrigger>
          <SelectContent>
            {sharedMcps.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>Tool name</Label>
        <Input
          value={(data.toolName as string) ?? ""}
          onChange={(e) => onChange({ toolName: e.target.value })}
          placeholder="tool_name"
        />
      </div>
    </div>
  );
}
