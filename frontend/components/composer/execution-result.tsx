"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function ExecutionResult({ output }: { output: unknown }) {
  const pretty = typeof output === "string" ? output : JSON.stringify(output, null, 2);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Result</CardTitle>
      </CardHeader>
      <CardContent>
        <pre className="bg-muted/30 max-h-96 overflow-auto rounded-md p-3 text-xs">{pretty}</pre>
      </CardContent>
    </Card>
  );
}
