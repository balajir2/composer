import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { components } from "@/lib/api/generated/schema";
import { AdminExecutionDiagnostics } from "./admin-execution-diagnostics";

type Diagnostics = components["schemas"]["AdminExecutionDiagnostics"];
const diagnostics: Diagnostics = {
  threadId: "thread-123",
  deliveryAttempts: 3,
  leaseOwner: "worker-1",
  leaseExpiresAt: "2026-11-26T10:05:00Z",
  failureCode: "timeout",
};

describe("AdminExecutionDiagnostics", () => {
  it("renders operational details only when diagnostics are supplied", () => {
    const { rerender } = render(<AdminExecutionDiagnostics diagnostics={null} />);
    expect(screen.queryByText(/thread-123/)).toBeNull();
    rerender(<AdminExecutionDiagnostics diagnostics={diagnostics} />);
    expect(screen.getByText("thread-123")).toBeInTheDocument();
    expect(screen.getByText("worker-1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("timeout")).toBeInTheDocument();
    expect(screen.getByText(/available references/i)).toBeInTheDocument();
  });

  it("does not expose secret or checkpoint fields in the component contract", () => {
    const accepted: Diagnostics = { threadId: "t", deliveryAttempts: 0 };
    expect(accepted).not.toHaveProperty("checkpoint");
    expect(accepted).not.toHaveProperty("payload");
    expect(accepted).not.toHaveProperty("secret");
  });
});
