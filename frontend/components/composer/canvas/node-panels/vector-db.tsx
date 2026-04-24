"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

const PROVIDER_OPTIONS = [
  { value: "pinecone", label: "Pinecone" },
  { value: "qdrant", label: "Qdrant" },
  { value: "chroma", label: "Chroma" },
  { value: "weaviate", label: "Weaviate" },
  { value: "milvus", label: "Milvus" },
];

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
        <Label htmlFor="vdb-provider">Provider</Label>
        <NativeSelect
          id="vdb-provider"
          value={(data.provider as string) ?? ""}
          onValueChange={(v) => onChange({ provider: v })}
          options={PROVIDER_OPTIONS}
          placeholder="Select provider"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="vdb-index">Index / collection name</Label>
        <Input
          id="vdb-index"
          value={(data.indexName as string) ?? ""}
          onChange={(e) => onChange({ indexName: e.target.value })}
          placeholder="my-index"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="vdb-query">Query</Label>
        <Input
          id="vdb-query"
          value={(data.query as string) ?? ""}
          onChange={(e) => onChange({ query: e.target.value })}
          placeholder="{{state.search_query}}"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="vdb-topk">Top K</Label>
        <Input
          id="vdb-topk"
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
