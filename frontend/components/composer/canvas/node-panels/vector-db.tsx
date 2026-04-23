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

export default function VectorDbPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
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
            <SelectItem value="pinecone">Pinecone</SelectItem>
            <SelectItem value="qdrant">Qdrant</SelectItem>
            <SelectItem value="chroma">Chroma</SelectItem>
            <SelectItem value="weaviate">Weaviate</SelectItem>
            <SelectItem value="milvus">Milvus</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>Index / collection name</Label>
        <Input
          value={(data.indexName as string) ?? ""}
          onChange={(e) => onChange({ indexName: e.target.value })}
          placeholder="my-index"
        />
      </div>

      <div className="space-y-2">
        <Label>Query</Label>
        <Input
          value={(data.query as string) ?? ""}
          onChange={(e) => onChange({ query: e.target.value })}
          placeholder="{{state.search_query}}"
        />
      </div>

      <div className="space-y-2">
        <Label>Top K</Label>
        <Input
          type="number"
          min={1}
          max={100}
          value={(data.topK as number) ?? 5}
          onChange={(e) => onChange({ topK: parseInt(e.target.value, 10) || 5 })}
          placeholder="5"
        />
      </div>
    </div>
  );
}
