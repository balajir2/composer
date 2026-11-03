import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlanCard } from "./plan-card";

describe("PlanCard", () => {
  it("lists stages", () => {
    render(
      <PlanCard
        stages={[
          { id: "s1", summary: "Ingest documents" },
          { id: "s2", summary: "Answer questions" },
        ]}
        onApprove={() => {}}
      />
    );
    expect(screen.getByText("Ingest documents")).toBeTruthy();
    expect(screen.getByText("Answer questions")).toBeTruthy();
  });
});
