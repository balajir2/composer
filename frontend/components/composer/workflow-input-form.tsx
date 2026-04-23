"use client";

import type { components } from "@/lib/api/generated/schema";
import { Button } from "@/components/ui/button";

type Workflow = components["schemas"]["WorkflowRead"];

export function WorkflowInputForm({ workflow }: { workflow: Workflow }) {
  void workflow;
  return <Button disabled>Run (implemented in Phase 10c Task 11)</Button>;
}
