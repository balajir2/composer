import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import UserApprovalPanel from "./user-approval";

describe("UserApprovalPanel", () => {
  it("editing the message field calls onChange with approvalMessage, not message", () => {
    const onChange = vi.fn();
    render(<UserApprovalPanel data={{}} onChange={onChange} currentNodeId="ua-1" />);
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Please review." },
    });
    expect(onChange).toHaveBeenCalledWith({ approvalMessage: "Please review." });
  });

  it("renders the canonical approvalMessage value", () => {
    const onChange = vi.fn();
    render(
      <UserApprovalPanel
        data={{ approvalMessage: "Approve this?" }}
        onChange={onChange}
        currentNodeId="ua-1"
      />
    );
    expect(screen.getByLabelText("Message")).toHaveValue("Approve this?");
  });

  it("migrates a legacy message field to approvalMessage on load", () => {
    const onChange = vi.fn();
    render(
      <UserApprovalPanel
        data={{ message: "Old message" }}
        onChange={onChange}
        currentNodeId="ua-1"
      />
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ approvalMessage: "Old message", message: undefined })
    );
  });

  it("does not overwrite an already-canonical approvalMessage with a stale legacy message", () => {
    const onChange = vi.fn();
    render(
      <UserApprovalPanel
        data={{ approvalMessage: "Current message", message: "Stale old message" }}
        onChange={onChange}
        currentNodeId="ua-1"
      />
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Message")).toHaveValue("Current message");
  });
});
