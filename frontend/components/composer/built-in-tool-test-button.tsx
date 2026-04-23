"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { testBuiltInTool } from "@/lib/api/admin";
import {
  InlineTestResult,
  type InlineTestState,
} from "@/components/composer/inline-test-result";

export function BuiltInToolTestButton({
  toolId,
}: {
  toolId: string;
  toolLabel?: string;
}) {
  const [state, setState] = useState<InlineTestState>({ kind: "idle" });

  const mutation = useMutation({
    mutationFn: () => testBuiltInTool(toolId),
    onMutate: () => setState({ kind: "pending" }),
    onSuccess: (res) => {
      setState(
        res.ok
          ? { kind: "success", message: res.message }
          : { kind: "error", message: res.message }
      );
    },
    onError: (err) =>
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Test failed.",
      }),
  });

  return (
    <div className="flex flex-col">
      <Button
        size="sm"
        variant="outline"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? "Testing…" : "Test"}
      </Button>
      <InlineTestResult state={state} onDismiss={() => setState({ kind: "idle" })} />
    </div>
  );
}
