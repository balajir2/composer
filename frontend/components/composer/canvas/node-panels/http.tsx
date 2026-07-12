"use client";

import { useEffect, useState } from "react";
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

const BODY_MODE_OPTIONS = [
  { value: "json", label: "JSON" },
  { value: "raw", label: "Raw text" },
];

type BodyMode = "json" | "raw";

function stringifyHeaders(headers: unknown): string {
  if (headers && typeof headers === "object" && Object.keys(headers).length > 0) {
    return JSON.stringify(headers, null, 2);
  }
  return "";
}

function stringifyBody(body: unknown): string {
  if (body === undefined || body === null) return "";
  if (typeof body === "string") return body;
  try {
    return JSON.stringify(body, null, 2);
  } catch {
    return "";
  }
}

/** Backend contract is dict[str, str] (src/engine/workflow.py HttpNodeData.http_headers). */
function parseHeaders(text: string): { value?: Record<string, string>; error?: string } {
  if (!text.trim()) return { value: {} };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "Invalid JSON." };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { error: "Headers must be a JSON object." };
  }
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof value !== "string") {
      return { error: `Header "${key}" must be a string value.` };
    }
  }
  return { value: parsed as Record<string, string> };
}

export default function HttpPanel({
  data,
  onChange,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  // Legacy-field migration (P0-0): the Designer used to write
  // method/url/headers/body instead of the canonical httpMethod/httpUrl/
  // httpHeaders/httpBody aliases the backend actually reads, so a
  // UI-configured HTTP node always failed at runtime. Backfill the
  // canonical fields and drop the legacy keys the moment this node is
  // opened, so the workflow converges on one source of truth. Never
  // overwrites an already-populated canonical field.
  useEffect(() => {
    const patch: Record<string, unknown> = {};
    let dirty = false;
    if (data.httpMethod === undefined && typeof data.method === "string") {
      patch.httpMethod = data.method;
      patch.method = undefined;
      dirty = true;
    }
    if (data.httpUrl === undefined && typeof data.url === "string") {
      patch.httpUrl = data.url;
      patch.url = undefined;
      dirty = true;
    }
    if (data.httpHeaders === undefined && typeof data.headers === "string") {
      const { value } = parseHeaders(data.headers);
      if (value) {
        patch.httpHeaders = value;
        patch.headers = undefined;
        dirty = true;
      }
    }
    if (data.httpBody === undefined && typeof data.body === "string") {
      // Legacy body was always a raw textarea labelled "Body (JSON)" — try
      // to parse it as JSON first; fall back to keeping it as raw text
      // rather than dropping content that doesn't parse.
      try {
        patch.httpBody = JSON.parse(data.body);
      } catch {
        patch.httpBody = data.body;
      }
      patch.body = undefined;
      dirty = true;
    }
    if (dirty) onChange(patch);
    // Re-run only when a different node is selected — `data` itself
    // changes on every keystroke and would otherwise re-trigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNodeId]);

  // Local textarea buffers, so invalid JSON never gets silently pushed
  // into the canonical field (which the backend types strictly) and never
  // gets silently discarded (the user's exact keystrokes stay visible
  // until they fix or abandon them).
  const [headersText, setHeadersText] = useState(() => stringifyHeaders(data.httpHeaders));
  const [headersError, setHeadersError] = useState<string | null>(null);
  const [bodyMode, setBodyMode] = useState<BodyMode>(
    typeof data.httpBody === "string" ? "raw" : "json"
  );
  const [bodyText, setBodyText] = useState(() => stringifyBody(data.httpBody));
  const [bodyError, setBodyError] = useState<string | null>(null);

  // This panel isn't remounted when the user selects a different node
  // (property-panel.tsx renders it without a `key`), so without this the
  // headers/body textareas would keep showing the PREVIOUSLY selected
  // node's content after switching to another HTTP node.
  useEffect(() => {
    setHeadersText(stringifyHeaders(data.httpHeaders));
    setHeadersError(null);
    setBodyMode(typeof data.httpBody === "string" ? "raw" : "json");
    setBodyText(stringifyBody(data.httpBody));
    setBodyError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNodeId]);

  function handleHeadersChange(text: string) {
    setHeadersText(text);
    const { value, error } = parseHeaders(text);
    if (error) {
      setHeadersError(error);
      return;
    }
    setHeadersError(null);
    onChange({ httpHeaders: value });
  }

  function handleBodyChange(text: string) {
    setBodyText(text);
    if (bodyMode === "raw") {
      setBodyError(null);
      onChange({ httpBody: text });
      return;
    }
    if (!text.trim()) {
      setBodyError(null);
      onChange({ httpBody: null });
      return;
    }
    try {
      const parsed: unknown = JSON.parse(text);
      setBodyError(null);
      onChange({ httpBody: parsed });
    } catch {
      setBodyError("Invalid JSON.");
    }
  }

  function handleBodyModeChange(mode: BodyMode) {
    setBodyMode(mode);
    if (mode === "raw") {
      setBodyError(null);
      onChange({ httpBody: bodyText });
      return;
    }
    if (!bodyText.trim()) {
      setBodyError(null);
      onChange({ httpBody: null });
      return;
    }
    try {
      const parsed: unknown = JSON.parse(bodyText);
      setBodyError(null);
      onChange({ httpBody: parsed });
    } catch {
      setBodyError("Invalid JSON — fix it or switch back to Raw text.");
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="http-method">Method</Label>
        <NativeSelect
          id="http-method"
          value={(data.httpMethod as string) ?? "GET"}
          onValueChange={(v) => onChange({ httpMethod: v })}
          options={METHOD_OPTIONS}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="http-url">URL</Label>
        <Input
          id="http-url"
          value={(data.httpUrl as string) ?? ""}
          onChange={(e) => onChange({ httpUrl: e.target.value })}
          placeholder="https://api.example.com/endpoint"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="http-headers">Headers (JSON object, string values)</Label>
        <Textarea
          id="http-headers"
          value={headersText}
          onChange={(e) => handleHeadersChange(e.target.value)}
          rows={3}
          placeholder='{"Authorization": "Bearer {{token}}"}'
          className="font-mono text-xs"
          aria-invalid={headersError !== null}
        />
        {headersError && <p className="text-xs text-destructive">{headersError}</p>}
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="http-body">Body</Label>
          <NativeSelect
            aria-label="Body mode"
            value={bodyMode}
            onValueChange={(v) => handleBodyModeChange(v as BodyMode)}
            options={BODY_MODE_OPTIONS}
          />
        </div>
        <Textarea
          id="http-body"
          value={bodyText}
          onChange={(e) => handleBodyChange(e.target.value)}
          rows={4}
          placeholder={bodyMode === "json" ? '{"key": "{{value}}"}' : "Raw request body"}
          className="font-mono text-xs"
          aria-invalid={bodyError !== null}
        />
        {bodyError && <p className="text-xs text-destructive">{bodyError}</p>}
      </div>

      <div className="space-y-2">
        <Label htmlFor="http-response-path">Response path (optional)</Label>
        <Input
          id="http-response-path"
          value={(data.responsePath as string) ?? ""}
          onChange={(e) => onChange({ responsePath: e.target.value })}
          placeholder="data.id"
        />
        <p className="text-[10px] text-muted-foreground">
          Dot-separated path into the JSON response to use as this node&apos;s output (e.g.{" "}
          <code className="font-mono">data.items.0.id</code>). Leave blank to use the full
          response body.
        </p>
      </div>
    </div>
  );
}
