import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Calendar } from "./calendar";

describe("Calendar", () => {
  it("renders the month of the given value and calls onChange on day click", () => {
    const onChange = vi.fn();
    render(<Calendar value={new Date(2026, 6, 20)} onChange={onChange} />);
    expect(screen.getByText("July 2026")).toBeInTheDocument();
    screen.getByRole("button", { name: "July 15, 2026" }).click();
    expect(onChange).toHaveBeenCalledWith(new Date(2026, 6, 15));
  });

  it("marks the selected day as pressed", () => {
    render(<Calendar value={new Date(2026, 6, 20)} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "July 20, 2026" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("navigating to the next month updates the header and day options", async () => {
    render(<Calendar value={new Date(2026, 6, 20)} onChange={vi.fn()} />);
    screen.getByRole("button", { name: "Next month" }).click();
    await waitFor(() => {
      expect(screen.getByText("August 2026")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "August 15, 2026" })).toBeInTheDocument();
  });

  it("defaults to the current month when no value is given", () => {
    render(<Calendar onChange={vi.fn()} />);
    const label = new Date().toLocaleDateString("en-US", { month: "long", year: "numeric" });
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
