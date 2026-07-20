import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import FileWritePanel from "./file-write";

describe("FileWritePanel", () => {
  it("offers html as a format option and calls onChange when selected", () => {
    const onChange = vi.fn();
    render(<FileWritePanel data={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Format"), {
      target: { value: "html" },
    });
    expect(onChange).toHaveBeenCalledWith({ format: "html" });
  });

  it("defaults to md and shows the existing format options too", () => {
    render(<FileWritePanel data={{}} onChange={vi.fn()} />);
    const select = screen.getByLabelText("Format") as HTMLSelectElement;
    expect(select.value).toBe("md");
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toEqual(["md", "docx", "pdf", "html"]);
  });
});
