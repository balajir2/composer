import { describe, it, expect } from "vitest";
import {
  formatDateISO,
  parseISODate,
  formatDateDisplay,
  formatDateTimeISO,
  parseISODateTime,
  formatDateTimeDisplay,
} from "./date-format";

describe("formatDateISO", () => {
  it("zero-pads single-digit month and day", () => {
    expect(formatDateISO(new Date(2026, 0, 5))).toBe("2026-01-05");
  });

  it("formats a two-digit month and day unchanged", () => {
    expect(formatDateISO(new Date(2026, 6, 20))).toBe("2026-07-20");
  });
});

describe("parseISODate", () => {
  it("parses a YYYY-MM-DD string into a local Date", () => {
    const date = parseISODate("2026-07-20");
    expect(date?.getFullYear()).toBe(2026);
    expect(date?.getMonth()).toBe(6);
    expect(date?.getDate()).toBe(20);
  });

  it("returns undefined for an empty or malformed string", () => {
    expect(parseISODate("")).toBeUndefined();
    expect(parseISODate("not-a-date")).toBeUndefined();
  });

  it("round-trips through formatDateISO", () => {
    const original = "2026-01-05";
    const date = parseISODate(original);
    expect(date && formatDateISO(date)).toBe(original);
  });
});

describe("formatDateDisplay", () => {
  it("formats a Date as a human-readable month/day/year", () => {
    expect(formatDateDisplay(new Date(2026, 6, 20))).toBe("Jul 20, 2026");
  });
});

describe("formatDateTimeISO", () => {
  it("combines a date and HH:mm time into YYYY-MM-DDTHH:mm:ss", () => {
    expect(formatDateTimeISO(new Date(2026, 6, 20), "14:30")).toBe("2026-07-20T14:30:00");
  });

  it("defaults to 00:00 when time is empty", () => {
    expect(formatDateTimeISO(new Date(2026, 6, 20), "")).toBe("2026-07-20T00:00:00");
  });

  it("zero-pads single-digit hour and minute", () => {
    expect(formatDateTimeISO(new Date(2026, 0, 5), "09:05")).toBe("2026-01-05T09:05:00");
  });
});

describe("parseISODateTime", () => {
  it("splits an ISO datetime string into its date and time parts", () => {
    const { date, time } = parseISODateTime("2026-07-20T14:30:00");
    expect(date && formatDateISO(date)).toBe("2026-07-20");
    expect(time).toBe("14:30");
  });

  it("returns an empty time when the string has no time part", () => {
    const { date, time } = parseISODateTime("2026-07-20");
    expect(date && formatDateISO(date)).toBe("2026-07-20");
    expect(time).toBe("");
  });
});

describe("formatDateTimeDisplay", () => {
  it("formats an afternoon time in 12-hour clock with AM/PM", () => {
    expect(formatDateTimeDisplay(new Date(2026, 6, 20), "14:30")).toBe("Jul 20, 2026 2:30 PM");
  });

  it("formats midnight as 12 AM", () => {
    expect(formatDateTimeDisplay(new Date(2026, 6, 20), "00:00")).toBe("Jul 20, 2026 12:00 AM");
  });

  it("falls back to date-only display when time is missing", () => {
    expect(formatDateTimeDisplay(new Date(2026, 6, 20), "")).toBe("Jul 20, 2026");
  });
});
