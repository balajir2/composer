"use client";

import { useQuery } from "@tanstack/react-query";
import { listDeploymentSettings } from "@/lib/api/admin";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { ToolEnabledToggle } from "@/components/composer/tool-enabled-toggle";
import { BuiltInToolTestButton } from "@/components/composer/built-in-tool-test-button";

const BUILT_IN_TOOLS = [
  { id: "tavily", label: "Tavily (web search)" },
  { id: "serper", label: "Serper (Google search)" },
  { id: "firecrawl", label: "Firecrawl (web scraping)" },
  { id: "browserless", label: "Browserless (browser automation)" },
] as const;

export default function AdminToolsPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["deployment-settings"],
    queryFn: () => listDeploymentSettings(),
  });

  function isToolEnabled(toolId: string): boolean {
    const key = `tool.${toolId}.enabled`;
    const setting = data?.find((s) => s.key === key);
    // Default is enabled when no setting row exists
    if (!setting) return true;
    return setting.value !== "false";
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-2xl font-semibold">Built-in tools</h2>
        <p className="text-sm text-muted-foreground">
          Native integrations compiled into Composer. Each tool requires an
          API key (Admin → LLM keys). Use <strong>Test</strong> to verify the
          currently-loaded key can reach the upstream service. Toggle a tool
          off here to hide it from designers without removing its key.
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          <strong>Adding a new tool without deploying code?</strong> Use an{" "}
          <a className="underline" href="/admin/mcp-servers">
            MCP server
          </a>{" "}
          instead — mark it <em>shared</em> and it appears in every
          designer&apos;s Tools palette alongside these built-ins.
        </p>
      </div>
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          title="Could not load settings"
          description="Try refreshing the page. If this keeps happening, check your network."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Tool</TableHead>
              <TableHead>Setting key</TableHead>
              <TableHead className="w-36">Status</TableHead>
              <TableHead className="w-28">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {BUILT_IN_TOOLS.map((tool) => {
              const settingKey = `tool.${tool.id}.enabled`;
              const enabled = isToolEnabled(tool.id);
              return (
                <TableRow key={tool.id}>
                  <TableCell className="font-medium">{tool.label}</TableCell>
                  <TableCell>
                    <code className="text-xs text-muted-foreground">{settingKey}</code>
                  </TableCell>
                  <TableCell>
                    <ToolEnabledToggle
                      settingKey={settingKey}
                      toolLabel={tool.label}
                      enabled={enabled}
                    />
                  </TableCell>
                  <TableCell>
                    <BuiltInToolTestButton toolId={tool.id} toolLabel={tool.label} />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
