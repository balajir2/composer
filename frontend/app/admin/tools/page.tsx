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
      <h2 className="text-2xl font-semibold">Built-in tools</h2>
      <p className="text-sm text-muted-foreground">
        Toggle which built-in tools are available to workflow designers.
      </p>
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
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
