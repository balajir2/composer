import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import EmailPanel from "./email";

describe("EmailPanel — From field", () => {
  it("explains that an empty From sends from the platform's verified sender", () => {
    render(<EmailPanel data={{}} onChange={vi.fn()} />);
    expect(
      screen.getByText(/leave empty to send from the platform's verified sender/i)
    ).toBeInTheDocument();
    expect(screen.getByLabelText("From")).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/verified sender/i)
    );
  });

  it("does not mark an empty From as invalid", () => {
    render(<EmailPanel data={{ emailFrom: "" }} onChange={vi.fn()} />);
    const from = screen.getByLabelText("From");
    expect(from).not.toHaveAttribute("aria-invalid", "true");
    expect(from).not.toBeRequired();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
