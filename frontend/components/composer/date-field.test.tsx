import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";
import {
  DatePickerButton,
  DateTimePickerButton,
  DatePickerInput,
  DateTimePickerInput,
} from "./date-field";

describe("DatePickerButton", () => {
  it("shows the formatted date when a value is set", () => {
    render(<DatePickerButton value="2026-07-20" onChange={vi.fn()} />);
    expect(screen.getByText("Jul 20, 2026")).toBeInTheDocument();
  });

  it("shows the placeholder when empty", () => {
    render(<DatePickerButton value="" onChange={vi.fn()} placeholder="Pick a date" />);
    expect(screen.getByText("Pick a date")).toBeInTheDocument();
  });
});

describe("DateTimePickerButton", () => {
  it("shows the formatted date and time when a value is set", () => {
    render(<DateTimePickerButton value="2026-07-20T14:30:00" onChange={vi.fn()} />);
    expect(screen.getByText("Jul 20, 2026 2:30 PM")).toBeInTheDocument();
  });

  it("shows the placeholder when empty", () => {
    render(
      <DateTimePickerButton value="" onChange={vi.fn()} placeholder="Pick date & time" />
    );
    expect(screen.getByText("Pick date & time")).toBeInTheDocument();
  });
});

function FormHarness({ type }: { type: "date" | "datetime" }) {
  const { register, setValue, watch } = useForm<{ f: string }>({ defaultValues: { f: "" } });
  const value = (watch("f") as string) ?? "";
  const Comp = type === "date" ? DatePickerInput : DateTimePickerInput;
  return (
    <Comp name="f" value={value} setValue={(v) => setValue("f", v)} registered={register("f")} />
  );
}

describe("DatePickerInput / DateTimePickerInput (react-hook-form wrapper)", () => {
  it("renders a hidden input registered under the field name", () => {
    render(<FormHarness type="date" />);
    expect(document.querySelector('input[type="hidden"][name="f"]')).not.toBeNull();
  });

  it("datetime variant also renders a hidden input registered under the field name", () => {
    render(<FormHarness type="datetime" />);
    expect(document.querySelector('input[type="hidden"][name="f"]')).not.toBeNull();
  });

  it("derives the trigger button's id from idPrefix and name", () => {
    render(
      <DatePickerInput
        name="report_date"
        value=""
        setValue={vi.fn()}
        registered={{ name: "report_date", onChange: vi.fn(), onBlur: vi.fn(), ref: vi.fn() }}
      />
    );
    expect(document.getElementById("f-report_date")).not.toBeNull();
  });
});
