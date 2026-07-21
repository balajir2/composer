import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import JoinPanel from "./join";

describe("JoinPanel", () => {
  it("renders a note that Join has no configurable fields", () => {
    const onChange = vi.fn();
    render(<JoinPanel data={{}} onChange={onChange} currentNodeId="join-1" />);
    expect(screen.getByText(/no configurable/i)).toBeInTheDocument();
  });
});
