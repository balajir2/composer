"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { NativeSelect } from "@/components/ui/native-select";

/**
 * Vector-DB node panel.
 *
 * Field names match the Pydantic aliases on `VectorDbNodeData`
 * (src/engine/workflow.py).  Every field uses the `vectorDb*` camelCase
 * alias that the backend serialises to and accepts on input, so saves
 * round-trip cleanly through the Workflow CRUD endpoints.
 *
 * The previous panel saved keys (`indexName`, `query`, `topK`,
 * `provider`) that didn't all map to backend fields — `indexName` and
 * `query` were never read by the executor, so any workflow saved with
 * the old shape would 422 or run with empty config.  Existing workflows
 * need to be re-opened and saved here to get a working configuration.
 */
const PROVIDER_OPTIONS = [
  { value: "pinecone", label: "Pinecone" },
  { value: "qdrant", label: "Qdrant" },
  { value: "chroma", label: "Chroma" },
  { value: "weaviate", label: "Weaviate" },
  { value: "milvus", label: "Milvus" },
];

const OPERATION_OPTIONS = [
  { value: "query", label: "Query — retrieve top-k similar chunks" },
  { value: "upsert", label: "Upsert — embed and insert chunks" },
];

const EMBEDDING_PROVIDER_OPTIONS = [{ value: "openai", label: "OpenAI" }];

// Per-provider hint for the endpoint field — paste-friendly examples
// since the connection URL shape varies wildly between vector DBs.
const ENDPOINT_PLACEHOLDERS: Record<string, string> = {
  pinecone: "https://my-index-abc123.svc.us-east-1.pinecone.io",
  qdrant: "https://xyz-cluster.qdrant.io  (or  http://localhost:6333)",
  chroma: "http://localhost:8000",
  weaviate: "https://my-instance.weaviate.network",
  milvus: "http://localhost:19530",
};

export default function VectorDbPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const operation = (data.vectorDbOperation as string) ?? "query";
  const provider = (data.vectorDbProvider as string) ?? "pinecone";
  const endpoint = (data.vectorDbEndpoint as string) ?? "";
  const documents = (data.vectorDbDocuments as string) ?? "";
  const chunkSize = (data.vectorDbChunkSize as number) ?? 1000;
  const chunkOverlap = (data.vectorDbChunkOverlap as number) ?? 100;
  const apiKey = (data.vectorDbApiKey as string) ?? "";
  const collection = (data.vectorDbCollection as string) ?? "";
  const namespace = (data.vectorDbNamespace as string) ?? "";
  const queryPrompt = (data.vectorDbQueryPrompt as string) ?? "";
  const topK = (data.vectorDbTopK as number) ?? 5;
  const scoreThreshold = (data.vectorDbScoreThreshold as number) ?? 0;
  const dimension = (data.vectorDbDimension as number) ?? 1536;
  const embeddingProvider =
    (data.vectorDbEmbeddingProvider as string) ?? "openai";
  const embeddingModel =
    (data.vectorDbEmbeddingModel as string) ?? "text-embedding-3-small";
  const outputVariable =
    (data.vectorDbOutputVariable as string) ?? "vectorDbResults";
  const textField = (data.vectorDbTextField as string) ?? "";
  const includeMetadata =
    (data.vectorDbIncludeMetadata as boolean | undefined) ?? true;
  const includeVector =
    (data.vectorDbIncludeVector as boolean | undefined) ?? false;
  const metadataFilter = (data.vectorDbMetadataFilter as string) ?? "";

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="vdb-operation">Operation</Label>
        <NativeSelect
          id="vdb-operation"
          value={operation}
          onValueChange={(v) => onChange({ vectorDbOperation: v })}
          options={OPERATION_OPTIONS}
        />
        <p className="text-xs text-muted-foreground">
          <strong>Query</strong>: embed the prompt and retrieve top-k
          matches. <strong>Upsert</strong>: embed each chunk in the
          documents expression and insert into the collection.
        </p>
      </div>

      {/* Connection */}
      <div className="space-y-2">
        <Label htmlFor="vdb-provider">Provider</Label>
        <NativeSelect
          id="vdb-provider"
          value={provider}
          onValueChange={(v) => onChange({ vectorDbProvider: v })}
          options={PROVIDER_OPTIONS}
          placeholder="Select provider"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="vdb-endpoint">Endpoint (host URL)</Label>
        <Input
          id="vdb-endpoint"
          value={endpoint}
          onChange={(e) => onChange({ vectorDbEndpoint: e.target.value })}
          placeholder={
            ENDPOINT_PLACEHOLDERS[provider] ?? "https://my-vector-db.example.com"
          }
        />
        <p className="text-xs text-muted-foreground">
          Full URL to your {provider} instance. Required.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="vdb-api-key">API key</Label>
        <Input
          id="vdb-api-key"
          type="password"
          value={apiKey}
          onChange={(e) => onChange({ vectorDbApiKey: e.target.value })}
          placeholder="paste credentials, or leave blank for unauthenticated dev instances"
          autoComplete="off"
        />
      </div>

      {/* Collection / namespace */}
      <div className="space-y-2">
        <Label htmlFor="vdb-collection">Collection / index name</Label>
        <Input
          id="vdb-collection"
          value={collection}
          onChange={(e) => onChange({ vectorDbCollection: e.target.value })}
          placeholder="my-collection"
        />
      </div>

      {provider === "pinecone" && (
        <div className="space-y-2">
          <Label htmlFor="vdb-namespace">
            Namespace{" "}
            <span className="font-normal text-muted-foreground">
              (Pinecone, optional)
            </span>
          </Label>
          <Input
            id="vdb-namespace"
            value={namespace}
            onChange={(e) =>
              onChange({ vectorDbNamespace: e.target.value || null })
            }
            placeholder="default"
          />
        </div>
      )}

      {/* Query mode — retrieval */}
      {operation === "query" && (
        <>
          <div className="space-y-2">
            <Label htmlFor="vdb-query">Query prompt</Label>
            <Textarea
              id="vdb-query"
              value={queryPrompt}
              onChange={(e) => onChange({ vectorDbQueryPrompt: e.target.value })}
              placeholder="What to search for. Reference upstream variables: {{question}}"
              rows={3}
            />
            <p className="text-xs text-muted-foreground">
              Embedded with the configured embedding model, then matched
              against vectors in the collection.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="vdb-topk">Top K</Label>
              <Input
                id="vdb-topk"
                type="number"
                min={1}
                max={100}
                value={topK}
                onChange={(e) =>
                  onChange({ vectorDbTopK: parseInt(e.target.value, 10) || 5 })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="vdb-score">Min score (0–1)</Label>
              <Input
                id="vdb-score"
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={scoreThreshold}
                onChange={(e) =>
                  onChange({
                    vectorDbScoreThreshold: parseFloat(e.target.value) || 0,
                  })
                }
              />
            </div>
          </div>
        </>
      )}

      {/* Upsert mode — ingestion */}
      {operation === "upsert" && (
        <>
          <div className="space-y-2">
            <Label htmlFor="vdb-documents">Documents expression</Label>
            <Textarea
              id="vdb-documents"
              value={documents}
              onChange={(e) =>
                onChange({ vectorDbDocuments: e.target.value || undefined })
              }
              placeholder="lastOutput  ←  raw text from upstream node (auto-chunked)
chunks  ←  pre-chunked list [{text, metadata?}, ...]
{{my_var}}  ←  Mustache resolves before eval"
              rows={3}
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              A simpleeval expression. Three accepted shapes: a single
              string (auto-chunked using the values below), a list of
              strings, or a list of <code className="font-mono">{"{id?, text, metadata?}"}</code>{" "}
              dicts (pre-chunked — recommended for production where you
              want stable ids).
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="vdb-chunk-size">Chunk size (chars)</Label>
              <Input
                id="vdb-chunk-size"
                type="number"
                min={100}
                max={20000}
                value={chunkSize}
                onChange={(e) =>
                  onChange({
                    vectorDbChunkSize: parseInt(e.target.value, 10) || 1000,
                  })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="vdb-chunk-overlap">Chunk overlap</Label>
              <Input
                id="vdb-chunk-overlap"
                type="number"
                min={0}
                max={chunkSize - 1}
                value={chunkOverlap}
                onChange={(e) =>
                  onChange({
                    vectorDbChunkOverlap: parseInt(e.target.value, 10) || 0,
                  })
                }
              />
            </div>
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            Char-window chunking only kicks in when the expression
            yields a single string. Pre-chunked lists are stored
            verbatim — chunk size is ignored.
          </p>
        </>
      )}

      {/* Embeddings */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="vdb-emb-provider">Embedding provider</Label>
          <NativeSelect
            id="vdb-emb-provider"
            value={embeddingProvider}
            onValueChange={(v) => onChange({ vectorDbEmbeddingProvider: v })}
            options={EMBEDDING_PROVIDER_OPTIONS}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="vdb-dim">Dimension</Label>
          <Input
            id="vdb-dim"
            type="number"
            min={1}
            value={dimension}
            onChange={(e) =>
              onChange({ vectorDbDimension: parseInt(e.target.value, 10) || 1536 })
            }
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="vdb-emb-model">Embedding model</Label>
        <Input
          id="vdb-emb-model"
          value={embeddingModel}
          onChange={(e) => onChange({ vectorDbEmbeddingModel: e.target.value })}
          placeholder="text-embedding-3-small"
        />
        <p className="text-xs text-muted-foreground">
          Must match the model used to embed your collection — mismatched
          dimensions return zero results.
        </p>
      </div>

      {/* Output */}
      <div className="space-y-2">
        <Label htmlFor="vdb-output-var">Output variable</Label>
        <Input
          id="vdb-output-var"
          value={outputVariable}
          onChange={(e) => onChange({ vectorDbOutputVariable: e.target.value })}
          placeholder="vectorDbResults"
        />
        <p className="text-xs text-muted-foreground">
          Downstream nodes reference results as{" "}
          <code className="font-mono">
            &#123;&#123;{outputVariable}.results&#125;&#125;
          </code>
          .
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="vdb-text-field">
          Text field{" "}
          <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Input
          id="vdb-text-field"
          value={textField}
          onChange={(e) =>
            onChange({ vectorDbTextField: e.target.value || null })
          }
          placeholder="content"
        />
        <p className="text-xs text-muted-foreground">
          Which metadata key holds the raw chunk text. Default tries
          common names (text, content, body).
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="vdb-meta-filter">
          Metadata filter (JSON){" "}
          <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Textarea
          id="vdb-meta-filter"
          value={metadataFilter}
          onChange={(e) =>
            onChange({ vectorDbMetadataFilter: e.target.value || null })
          }
          placeholder='{"source": "docs"}'
          rows={2}
          className="font-mono text-xs"
        />
      </div>

      <div className="flex items-center justify-between rounded-md border px-3 py-2">
        <div>
          <p className="text-sm font-medium">Include metadata</p>
          <p className="text-xs text-muted-foreground">
            Return per-result metadata alongside scores.
          </p>
        </div>
        <input
          type="checkbox"
          checked={includeMetadata}
          onChange={(e) =>
            onChange({ vectorDbIncludeMetadata: e.target.checked })
          }
          className="h-4 w-4"
        />
      </div>

      <div className="flex items-center justify-between rounded-md border px-3 py-2">
        <div>
          <p className="text-sm font-medium">Include vectors</p>
          <p className="text-xs text-muted-foreground">
            Return raw embeddings — large payload; off by default.
          </p>
        </div>
        <input
          type="checkbox"
          checked={includeVector}
          onChange={(e) =>
            onChange({ vectorDbIncludeVector: e.target.checked })
          }
          className="h-4 w-4"
        />
      </div>
    </div>
  );
}
