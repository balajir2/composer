"use client";

import { useState, useEffect } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { NativeSelect } from "@/components/ui/native-select";

// Must match REDACTED_MARKER in src/security/encryption.py — the backend
// never returns the real token once it's saved, only this marker.
const TOKEN_REDACTED_MARKER = "••••••••";

const OPERATION_OPTIONS = [
  { value: "create_or_update_page", label: "Create or update page" },
  { value: "get_page", label: "Get page" },
  { value: "get_property", label: "Get property" },
  { value: "set_property", label: "Set property" },
];

export default function ConfluencePanel({
  data,
  onChange,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  const operation = (data.operation as string) || "create_or_update_page";
  const rawApiToken = (data.apiToken as string) ?? "";
  const apiTokenIsRedacted = rawApiToken === TOKEN_REDACTED_MARKER;

  const spaceKey = (data.spaceKey as string) ?? "";
  const parentPageId = (data.parentPageId as string) ?? "";
  const title = (data.title as string) ?? "";
  const bodyStorageHtml = (data.bodyStorageHtml as string) ?? "";
  const pageId = (data.pageId as string) ?? "";
  const propertyKey = (data.propertyKey as string) ?? "";
  const propertyValue = (data.propertyValue as string) ?? "";

  // Labels is a free-typed comma-separated list. Its displayed text must be
  // kept in local state — NOT re-derived from the parsed `labels` array on
  // every keystroke — because the parsed array drops empty tail tokens
  // (e.g. the trailing comma while typing "weekly-report, "), which would
  // snap the controlled value back and silently merge the next character
  // into the previous entry. Only re-seed from the prop when the mode
  // actually changes externally OR when the selected node changes —
  // ConfluencePanel stays mounted across node selection (no key={node.id}),
  // so a plain [operation] dependency would leave the previous node's stale
  // Labels text on screen when switching to another Confluence node,
  // corrupting the new node's labels on the next edit. Matches the
  // [currentNodeId] convention used by jira.tsx/extract.tsx/http.tsx for
  // this same class of bug.
  const [labelsText, setLabelsText] = useState(() =>
    ((data.labels as string[] | undefined) ?? []).join(", ")
  );
  useEffect(() => {
    setLabelsText(((data.labels as string[] | undefined) ?? []).join(", "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operation, currentNodeId]);

  const needsSpaceAndTitle =
    operation === "create_or_update_page" || operation === "get_page";
  const needsPageId = operation === "get_property" || operation === "set_property";

  return (
    <div className="space-y-4">
      {/* Credentials */}
      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">
          Confluence Connection
        </p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="confluence-domain" className="text-xs">
              Domain
            </Label>
            <Input
              id="confluence-domain"
              value={(data.domain as string) ?? ""}
              onChange={(e) => onChange({ domain: e.target.value })}
              placeholder="your-org.atlassian.net"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="confluence-email" className="text-xs">
              Email
            </Label>
            <Input
              id="confluence-email"
              value={(data.email as string) ?? ""}
              onChange={(e) => onChange({ email: e.target.value })}
              placeholder="you@example.com"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="confluence-api-token" className="text-xs">
              API Token
            </Label>
            <Input
              id="confluence-api-token"
              type="password"
              value={apiTokenIsRedacted ? "" : rawApiToken}
              onChange={(e) => onChange({ apiToken: e.target.value })}
              placeholder={
                apiTokenIsRedacted
                  ? "API token is set — leave blank to keep, type to replace"
                  : "••••••••"
              }
              className="font-mono text-xs"
            />
            <p className="text-[10px] text-muted-foreground">
              Generate at{" "}
              <a
                href="https://id.atlassian.com/manage/api-tokens"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                id.atlassian.com/manage/api-tokens
              </a>
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="confluence-operation">Operation</Label>
        <NativeSelect
          id="confluence-operation"
          value={operation}
          onValueChange={(v) => onChange({ operation: v })}
          options={OPERATION_OPTIONS}
        />
      </div>

      {needsSpaceAndTitle && (
        <>
          <div className="space-y-2">
            <Label htmlFor="confluence-space-key">Space key</Label>
            <Input
              id="confluence-space-key"
              value={spaceKey}
              onChange={(e) => onChange({ spaceKey: e.target.value })}
              placeholder="MB"
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confluence-title">Title</Label>
            <Input
              id="confluence-title"
              value={title}
              onChange={(e) => onChange({ title: e.target.value })}
              placeholder="{{project_name}} - Weekly Delivery Report - {{reporting_date}}"
              className="font-mono text-xs"
            />
          </div>
        </>
      )}

      {operation === "create_or_update_page" && (
        <>
          <div className="space-y-2">
            <Label htmlFor="confluence-parent-page-id">
              Parent page ID{" "}
              <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Input
              id="confluence-parent-page-id"
              value={parentPageId}
              onChange={(e) => onChange({ parentPageId: e.target.value || null })}
              placeholder="123456"
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confluence-body">Body (storage format HTML)</Label>
            <Textarea
              id="confluence-body"
              value={bodyStorageHtml}
              onChange={(e) => onChange({ bodyStorageHtml: e.target.value })}
              rows={6}
              placeholder="<h1>Weekly Delivery Summary</h1><p>{{narrative}}</p>"
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confluence-labels">Labels</Label>
            <Input
              id="confluence-labels"
              value={labelsText}
              onChange={(e) => {
                const text = e.target.value;
                setLabelsText(text);
                onChange({
                  labels: text
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                });
              }}
              placeholder="weekly-report, adobe-target"
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              Comma-separated. Reconciled exactly to this set on every run —
              labels not listed here are removed.
            </p>
          </div>
        </>
      )}

      {needsPageId && (
        <div className="space-y-2">
          <Label htmlFor="confluence-page-id">Page ID</Label>
          <Input
            id="confluence-page-id"
            value={pageId}
            onChange={(e) => onChange({ pageId: e.target.value })}
            placeholder="{{find_last_week.pageId}}"
            className="font-mono text-xs"
          />
        </div>
      )}

      {needsPageId && (
        <div className="space-y-2">
          <Label htmlFor="confluence-property-key">Property key</Label>
          <Input
            id="confluence-property-key"
            value={propertyKey}
            onChange={(e) => onChange({ propertyKey: e.target.value })}
            placeholder="metrics_snapshot"
            className="font-mono text-xs"
          />
        </div>
      )}

      {operation === "set_property" && (
        <div className="space-y-2">
          <Label htmlFor="confluence-property-value">
            Property value (JSON or text)
          </Label>
          <Textarea
            id="confluence-property-value"
            value={propertyValue}
            onChange={(e) => onChange({ propertyValue: e.target.value })}
            rows={4}
            placeholder='{{compute_metrics.output}}  or  {"total": 42}'
            className="font-mono text-xs"
          />
        </div>
      )}
    </div>
  );
}
