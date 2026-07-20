# Start-node Date/DateTime Field Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `date` and `datetime` field types to the Start node, rendering a calendar picker in the Designer panel and both runtime input forms, serializing to plain ISO-8601 text.

**Architecture:** A hand-rolled `Calendar` grid + a new Base-UI-backed `Popover` primitive compose into two controlled widgets (`DatePickerButton`/`DateTimePickerButton`), plus thin react-hook-form wrappers (`DatePickerInput`/`DateTimePickerInput`) matching the existing `DocumentField` register/setValue convention. Three consumers wire them in: the Designer's Start panel (default value), the production run form, and the "run draft" dialog. No backend changes — `StartInputVariable.type` is already a free-form string and the submitted value stays plain text.

**Tech Stack:** Next.js 14, `@base-ui/react` (Popover — new primitive for this repo), `react-hook-form` + `zod`, Vitest + Testing Library, Playwright.

**Spec:** [`docs/superpowers/specs/2026-07-20-start-node-date-field-design.md`](../specs/2026-07-20-start-node-date-field-design.md)

---

### Task 1: `date-format.ts` — pure ISO formatting/parsing helpers

**Files:**
- Create: `frontend/lib/date-format.ts`
- Test: `frontend/lib/date-format.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/date-format.test.ts
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/date-format.test.ts`
Expected: FAIL — `Cannot find module './date-format'`

- [ ] **Step 3: Write the implementation**

```ts
// frontend/lib/date-format.ts

// Plain ISO-8601 text is the wire format for Start-node "date"/"datetime"
// fields — no date library, no timezone offset. See
// docs/superpowers/specs/2026-07-20-start-node-date-field-design.md.

export function formatDateISO(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function parseISODate(text: string): Date | undefined {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  if (!match) return undefined;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  return Number.isNaN(date.getTime()) ? undefined : date;
}

export function formatDateDisplay(date: Date): string {
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function formatDateTimeISO(date: Date, time: string): string {
  const match = /^(\d{2}):(\d{2})/.exec(time);
  const hh = match ? match[1] : "00";
  const mm = match ? match[2] : "00";
  return `${formatDateISO(date)}T${hh}:${mm}:00`;
}

export function parseISODateTime(text: string): { date: Date | undefined; time: string } {
  return {
    date: parseISODate(text),
    time: /T(\d{2}:\d{2})/.exec(text)?.[1] ?? "",
  };
}

export function formatDateTimeDisplay(date: Date, time: string): string {
  const match = /^(\d{2}):(\d{2})/.exec(time);
  if (!match) return formatDateDisplay(date);
  const hours = Number(match[1]);
  const period = hours >= 12 ? "PM" : "AM";
  const hour12 = hours % 12 === 0 ? 12 : hours % 12;
  return `${formatDateDisplay(date)} ${hour12}:${match[2]} ${period}`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/date-format.test.ts`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/date-format.ts frontend/lib/date-format.test.ts
git commit -m "$(cat <<'EOF'
feat: add ISO date/datetime formatting helpers for Start-node date fields

Pure functions, no date library — matches the plain-text wire format
every other Start input already uses.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `calendar.tsx` — hand-rolled month-grid primitive

**Files:**
- Create: `frontend/components/ui/calendar.tsx`
- Test: `frontend/components/ui/calendar.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/ui/calendar.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

  it("navigating to the next month updates the header and day options", () => {
    render(<Calendar value={new Date(2026, 6, 20)} onChange={vi.fn()} />);
    screen.getByRole("button", { name: "Next month" }).click();
    expect(screen.getByText("August 2026")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "August 15, 2026" })).toBeInTheDocument();
  });

  it("defaults to the current month when no value is given", () => {
    render(<Calendar onChange={vi.fn()} />);
    const label = new Date().toLocaleDateString("en-US", { month: "long", year: "numeric" });
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/ui/calendar.test.tsx`
Expected: FAIL — `Cannot find module './calendar'`

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/components/ui/calendar.tsx
"use client"

import * as React from "react"
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"

import { cn } from "@/lib/utils"

const WEEKDAY_LABELS = ["S", "M", "T", "W", "T", "F", "S"]

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

function isSameDay(a: Date | undefined, b: Date): boolean {
  return (
    !!a &&
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

export interface CalendarProps {
  value?: Date
  onChange: (date: Date) => void
  className?: string
}

export function Calendar({ value, onChange, className }: CalendarProps) {
  // Lazy initializer seeds the displayed month from `value` once, at
  // mount. No effect re-syncing this on every `value` change: Calendar
  // only mounts while its Popover is open, so a fresh instance already
  // seeds correctly each time it opens. Re-syncing on every re-render
  // would reset the user's in-progress month navigation whenever an
  // unrelated field elsewhere in the same form triggers a re-render
  // (DatePickerInput's `value` comes from `form.watch`, which
  // re-renders on *any* field change and produces a new Date object
  // each time even when the underlying ISO string is unchanged).
  const [viewMonth, setViewMonth] = React.useState(() => startOfMonth(value ?? new Date()))

  const today = new Date()
  const firstWeekday = viewMonth.getDay()
  const daysInMonth = new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 0).getDate()
  const cells: Array<Date | null> = [
    ...Array<null>(firstWeekday).fill(null),
    ...Array.from(
      { length: daysInMonth },
      (_, i) => new Date(viewMonth.getFullYear(), viewMonth.getMonth(), i + 1)
    ),
  ]

  return (
    <div className={cn("w-64 p-2", className)} data-slot="calendar">
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          aria-label="Previous month"
          onClick={() =>
            setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() - 1, 1))
          }
          className="rounded-md p-1 hover:bg-muted"
        >
          <ChevronLeftIcon className="h-4 w-4" />
        </button>
        <span className="text-sm font-medium">
          {viewMonth.toLocaleDateString("en-US", { month: "long", year: "numeric" })}
        </span>
        <button
          type="button"
          aria-label="Next month"
          onClick={() =>
            setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 1))
          }
          className="rounded-md p-1 hover:bg-muted"
        >
          <ChevronRightIcon className="h-4 w-4" />
        </button>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-xs text-muted-foreground">
        {WEEKDAY_LABELS.map((label, i) => (
          <span key={i}>{label}</span>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((date, i) =>
          date ? (
            <button
              key={i}
              type="button"
              aria-label={date.toLocaleDateString("en-US", {
                month: "long",
                day: "numeric",
                year: "numeric",
              })}
              aria-pressed={isSameDay(value, date)}
              onClick={() => onChange(date)}
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-md text-xs hover:bg-muted",
                isSameDay(value, date) &&
                  "bg-primary text-primary-foreground hover:bg-primary/80",
                !isSameDay(value, date) && isSameDay(today, date) && "font-semibold text-primary"
              )}
            >
              {date.getDate()}
            </button>
          ) : (
            <span key={i} />
          )
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/ui/calendar.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/ui/calendar.tsx frontend/components/ui/calendar.test.tsx
git commit -m "$(cat <<'EOF'
feat: add hand-rolled Calendar month-grid primitive

No react-day-picker/date-fns dependency — the repo's shadcn-derived
UI kit is built on Base UI, not the Radix stack those libraries
assume, and a full date library is unnecessary scope for a
month-grid picker.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `popover.tsx` — new Base UI Popover primitive

**Files:**
- Create: `frontend/components/ui/popover.tsx`

No dedicated test file — this mirrors `dropdown-menu.tsx`, which also ships without one; its behavior is exercised through the components that consume it (Task 4) and the Playwright e2e flow (Task 9). Base-UI-portaled open/close interactions have proven unreliable to drive from jsdom in this repo before (see `native-select.tsx`'s comment on why `Select` was replaced), so this repo's convention is to keep Vitest coverage on the pure/presentational pieces and defer floating-UI interaction testing to a real browser.

- [ ] **Step 1: Write the implementation**

```tsx
// frontend/components/ui/popover.tsx
"use client"

import * as React from "react"
import { Popover as PopoverPrimitive } from "@base-ui/react/popover"

import { cn } from "@/lib/utils"

function Popover({ ...props }: PopoverPrimitive.Root.Props) {
  return <PopoverPrimitive.Root data-slot="popover" {...props} />
}

function PopoverTrigger({ ...props }: PopoverPrimitive.Trigger.Props) {
  return <PopoverPrimitive.Trigger data-slot="popover-trigger" {...props} />
}

function PopoverContent({
  align = "start",
  alignOffset = 0,
  side = "bottom",
  sideOffset = 4,
  className,
  ...props
}: PopoverPrimitive.Popup.Props &
  Pick<PopoverPrimitive.Positioner.Props, "align" | "alignOffset" | "side" | "sideOffset">) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        className="isolate z-50 outline-none"
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
      >
        <PopoverPrimitive.Popup
          data-slot="popover-content"
          className={cn(
            "z-50 w-auto origin-(--transform-origin) rounded-lg bg-popover p-2 text-popover-foreground shadow-md ring-1 ring-foreground/10 duration-100 outline-none data-[side=bottom]:slide-in-from-top-2 data-[side=inline-end]:slide-in-from-left-2 data-[side=inline-start]:slide-in-from-right-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
            className
          )}
          {...props}
        />
      </PopoverPrimitive.Positioner>
    </PopoverPrimitive.Portal>
  )
}

export { Popover, PopoverTrigger, PopoverContent }
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run type-check`
Expected: no errors from `components/ui/popover.tsx`

- [ ] **Step 3: Commit**

```bash
git add frontend/components/ui/popover.tsx
git commit -m "$(cat <<'EOF'
feat: add Popover primitive (first in the repo, built on Base UI)

Mirrors dropdown-menu.tsx's Root/Trigger/Portal/Positioner/Popup
wrapping pattern. Needed by the new date/datetime pickers; every
other floating-content primitive already exists (menu, select,
dialog) — this fills the one gap.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `date-field.tsx` — composed picker widgets

**Files:**
- Create: `frontend/components/composer/date-field.tsx`
- Test: `frontend/components/composer/date-field.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/composer/date-field.test.tsx
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/composer/date-field.test.tsx`
Expected: FAIL — `Cannot find module './date-field'`

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/components/composer/date-field.tsx
"use client";

import { useState } from "react";
import type { useForm } from "react-hook-form";
import { CalendarIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  formatDateDisplay,
  formatDateISO,
  formatDateTimeDisplay,
  formatDateTimeISO,
  parseISODate,
  parseISODateTime,
} from "@/lib/date-format";

/**
 * Controlled date picker: trigger button showing the formatted date +
 * a popover with the calendar grid. Stores/emits plain "YYYY-MM-DD"
 * text — the same wire format every Start input already uses.
 */
export function DatePickerButton({
  id,
  value,
  onChange,
  placeholder = "Select a date",
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const date = parseISODate(value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            id={id}
            type="button"
            variant="outline"
            className="w-full justify-between font-normal"
          />
        }
      >
        <span>{date ? formatDateDisplay(date) : placeholder}</span>
        <CalendarIcon className="h-3.5 w-3.5 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent>
        <Calendar
          value={date}
          onChange={(d) => {
            onChange(formatDateISO(d));
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}

/**
 * Same shell as DatePickerButton, plus a native time input for the
 * time-of-day portion (no custom time-scroll widget — see design doc's
 * "don't over-engineer" decision). Stores/emits "YYYY-MM-DDTHH:mm:ss".
 * Does not auto-close on date pick — the user still needs to set the
 * time.
 */
export function DateTimePickerButton({
  id,
  value,
  onChange,
  placeholder = "Select a date & time",
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const { date, time } = parseISODateTime(value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            id={id}
            type="button"
            variant="outline"
            className="w-full justify-between font-normal"
          />
        }
      >
        <span>{date ? formatDateTimeDisplay(date, time) : placeholder}</span>
        <CalendarIcon className="h-3.5 w-3.5 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent>
        <div className="space-y-2">
          <Calendar value={date} onChange={(d) => onChange(formatDateTimeISO(d, time || "00:00"))} />
          <input
            type="time"
            value={time}
            onChange={(e) => onChange(formatDateTimeISO(date ?? new Date(), e.target.value))}
            className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm"
          />
        </div>
      </PopoverContent>
    </Popover>
  );
}

type RegisterReturn = ReturnType<ReturnType<typeof useForm>["register"]>;

/**
 * react-hook-form-flavored wrapper matching DocumentField's
 * register/setValue convention (see document-field.tsx) so
 * WorkflowInputForm and RunDraftDialog wire it exactly like every
 * other custom-widget Start input.
 */
export function DatePickerInput({
  name,
  value,
  setValue,
  registered,
  idPrefix = "f",
}: {
  name: string;
  value: string;
  setValue: (value: string) => void;
  registered: RegisterReturn;
  idPrefix?: string;
}) {
  return (
    <>
      <input type="hidden" {...registered} />
      <DatePickerButton id={`${idPrefix}-${name}`} value={value} onChange={setValue} />
    </>
  );
}

export function DateTimePickerInput({
  name,
  value,
  setValue,
  registered,
  idPrefix = "f",
}: {
  name: string;
  value: string;
  setValue: (value: string) => void;
  registered: RegisterReturn;
  idPrefix?: string;
}) {
  return (
    <>
      <input type="hidden" {...registered} />
      <DateTimePickerButton id={`${idPrefix}-${name}`} value={value} onChange={setValue} />
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/composer/date-field.test.tsx`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/composer/date-field.tsx frontend/components/composer/date-field.test.tsx
git commit -m "$(cat <<'EOF'
feat: add DatePickerButton/DateTimePickerButton widgets + RHF wrappers

Composes Calendar + Popover into controlled {value, onChange: string}
widgets, plus DatePickerInput/DateTimePickerInput wrappers matching
DocumentField's register/setValue convention for the two
react-hook-form-backed Start input forms.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `start-node-schema.ts` — accept `date`/`datetime` as Start field types

**Files:**
- Modify: `frontend/lib/start-node-schema.ts:10,35,87-90`
- Test: Create `frontend/lib/start-node-schema.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/start-node-schema.test.ts
import { describe, it, expect } from "vitest";
import { startNodeSpec } from "./start-node-schema";

function wf(inputVariables: unknown[]) {
  return { nodes: [{ type: "start", data: { inputVariables } }] };
}

describe("startNodeSpec — date/datetime field types", () => {
  it("passes through a date field with its declared type", () => {
    const { fields } = startNodeSpec(
      wf([{ name: "report_date", type: "date", required: true }])
    );
    expect(fields[0]?.type).toBe("date");
  });

  it("passes through a datetime field with its declared type", () => {
    const { fields } = startNodeSpec(
      wf([{ name: "extract_timestamp", type: "datetime", required: true }])
    );
    expect(fields[0]?.type).toBe("datetime");
  });

  it("validates a required date field as a non-empty string", () => {
    const { schema } = startNodeSpec(
      wf([{ name: "report_date", type: "date", required: true }])
    );
    expect(schema.safeParse({ report_date: "" }).success).toBe(false);
    expect(schema.safeParse({ report_date: "2026-07-20" }).success).toBe(true);
  });

  it("validates an optional datetime field", () => {
    const { schema } = startNodeSpec(
      wf([{ name: "extract_timestamp", type: "datetime", required: false }])
    );
    expect(schema.safeParse({}).success).toBe(true);
    expect(schema.safeParse({ extract_timestamp: "2026-07-20T14:30:00" }).success).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/start-node-schema.test.ts`
Expected: FAIL — `fields[0]?.type` is `"text"`, not `"date"` (unknown types fall back to `"text"` in `normalizeField`)

- [ ] **Step 3: Update the implementation**

In `frontend/lib/start-node-schema.ts`, update the `StartField.type` union (line 10):

```ts
  type: "text" | "number" | "json" | "boolean" | "document" | "date" | "datetime";
```

Update the allowed-types list in `normalizeField` (line 35):

```ts
  const type: StartField["type"] = (
    ["text", "number", "boolean", "json", "document", "date", "datetime"] as const
  ).includes(aliased as StartField["type"])
    ? (aliased as StartField["type"])
    : "text";
```

Update the zod-building switch (lines 87–90) so `date`/`datetime` validate exactly like `text` — a plain string, required or optional:

```ts
    switch (f.type) {
      case "text":
      case "date":
      case "datetime":
        field = f.required ? z.string().min(1, "required") : z.string().optional();
        break;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/start-node-schema.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/start-node-schema.ts frontend/lib/start-node-schema.test.ts
git commit -m "$(cat <<'EOF'
feat: accept date/datetime as Start-node field types

Both validate as plain strings, identically to "text" — no new
validation branch, matching the design's "text is the only wire
format" decision.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Designer panel (`start.tsx`) — Type dropdown + default-value picker

**Files:**
- Modify: `frontend/components/composer/canvas/node-panels/start.tsx`
- Test: Create `frontend/components/composer/canvas/node-panels/start.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/composer/canvas/node-panels/start.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import StartPanel from "./start";

function dataWithField(overrides: Record<string, unknown> = {}) {
  return {
    inputVariables: [
      { name: "report_date", type: "date", required: false, description: "", ...overrides },
    ],
  };
}

describe("StartPanel — date/datetime field types", () => {
  it("offers Date and Date & Time in the type dropdown", () => {
    render(<StartPanel data={dataWithField()} onChange={vi.fn()} />);
    const select = screen.getByDisplayValue("date") as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("date");
    expect(optionValues).toContain("datetime");
  });

  it("renders a date picker trigger instead of a plain input for a date-typed field", () => {
    render(<StartPanel data={dataWithField()} onChange={vi.fn()} />);
    expect(screen.getByText("Select a date")).toBeInTheDocument();
  });

  it("renders a date+time picker trigger for a datetime-typed field", () => {
    render(<StartPanel data={dataWithField({ type: "datetime" })} onChange={vi.fn()} />);
    expect(screen.getByText("Select a date & time")).toBeInTheDocument();
  });

  it("switching a field's type to date calls onChange with the new type", () => {
    const onChange = vi.fn();
    render(
      <StartPanel
        data={{
          inputVariables: [{ name: "x", type: "text", required: false, description: "" }],
        }}
        onChange={onChange}
      />
    );
    fireEvent.change(screen.getByDisplayValue("text"), { target: { value: "date" } });
    expect(onChange).toHaveBeenCalledWith({
      inputVariables: [{ name: "x", type: "date", required: false, description: "" }],
      inputs: undefined,
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/composer/canvas/node-panels/start.test.tsx`
Expected: FAIL — no "date"/"datetime" options exist yet, and the plain `<Input>` shows a blank value, not "Select a date"

- [ ] **Step 3: Update the implementation**

In `frontend/components/composer/canvas/node-panels/start.tsx`, add the import:

```tsx
import { DatePickerButton, DateTimePickerButton } from "@/components/composer/date-field";
```

Update the `InputField.type` union and `ALLOWED_TYPES` (lines 16–24):

```tsx
type InputField = {
  name: string;
  type: "text" | "number" | "boolean" | "json" | "document" | "date" | "datetime";
  required: boolean;
  description?: string;
  defaultValue?: unknown;
};

const ALLOWED_TYPES = ["text", "number", "boolean", "json", "document", "date", "datetime"] as const;
```

Update `TYPE_OPTIONS` (lines 26–32):

```tsx
const TYPE_OPTIONS = [
  { value: "text", label: "text" },
  { value: "number", label: "number" },
  { value: "boolean", label: "boolean" },
  { value: "json", label: "json (object / array)" },
  { value: "document", label: "document (PDF / DOCX / MD / TXT upload)" },
  { value: "date", label: "date" },
  { value: "datetime", label: "date & time" },
];
```

Update the default-value rendering branch (lines 138–173) to add `date`/`datetime` cases before the final plain-`<Input>` fallback:

```tsx
            {field.type === "document" ? (
              // No default value for documents — files are always
              // uploaded fresh per run.  Show a one-line note instead
              // so the panel structure stays predictable.
              <p className="text-xs text-muted-foreground">
                End-users get a file picker (PDF / DOCX / MD / TXT, max
                10MB). Extracted text flows downstream as a regular
                string variable — reference as{" "}
                <code className="font-mono">
                  &#123;&#123;{field.name || "name"}&#125;&#125;
                </code>
                .
              </p>
            ) : field.type === "date" ? (
              <div className="space-y-1">
                <Label className="text-xs">Default value (optional)</Label>
                <DatePickerButton
                  value={
                    field.defaultValue === undefined || field.defaultValue === null
                      ? ""
                      : String(field.defaultValue)
                  }
                  onChange={(v) => updateField(i, { defaultValue: v || undefined })}
                />
              </div>
            ) : field.type === "datetime" ? (
              <div className="space-y-1">
                <Label className="text-xs">Default value (optional)</Label>
                <DateTimePickerButton
                  value={
                    field.defaultValue === undefined || field.defaultValue === null
                      ? ""
                      : String(field.defaultValue)
                  }
                  onChange={(v) => updateField(i, { defaultValue: v || undefined })}
                />
              </div>
            ) : (
              <div className="space-y-1">
                <Label className="text-xs">Default value (optional)</Label>
                <Input
                  value={
                    field.defaultValue === undefined || field.defaultValue === null
                      ? ""
                      : String(field.defaultValue)
                  }
                  onChange={(e) =>
                    updateField(i, {
                      defaultValue: e.target.value === "" ? undefined : e.target.value,
                    })
                  }
                  placeholder={
                    field.type === "json"
                      ? '{"key": "value"}'
                      : "leave blank for none"
                  }
                  className="h-7 text-xs"
                />
              </div>
            )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/composer/canvas/node-panels/start.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/composer/canvas/node-panels/start.tsx frontend/components/composer/canvas/node-panels/start.test.tsx
git commit -m "$(cat <<'EOF'
feat: add date/datetime types to the Start node Designer panel

The Type dropdown offers "date" and "date & time"; a field's default
value renders the calendar picker instead of a plain text box for
either.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Production run form (`workflow-input-form.tsx`)

**Files:**
- Modify: `frontend/components/composer/workflow-input-form.tsx:81-102`
- Modify (add tests): `frontend/components/composer/workflow-input-form.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/components/composer/workflow-input-form.test.tsx`:

```tsx
  it("renders a date picker trigger button (not a plain text input) for a date-typed field", () => {
    const wf: Workflow = {
      ...baseWf,
      nodes: [
        {
          type: "start",
          data: {
            inputVariables: [
              { name: "report_date", description: "Report date", type: "date", required: true },
            ],
          },
        },
      ],
    };
    render(wrap(<WorkflowInputForm workflow={wf} />));
    expect(screen.getByLabelText(/Report date/).tagName).toBe("BUTTON");
  });

  it("renders a date+time picker trigger button for a datetime-typed field", () => {
    const wf: Workflow = {
      ...baseWf,
      nodes: [
        {
          type: "start",
          data: {
            inputVariables: [
              {
                name: "extract_timestamp",
                description: "Extract timestamp",
                type: "datetime",
                required: true,
              },
            ],
          },
        },
      ],
    };
    render(wrap(<WorkflowInputForm workflow={wf} />));
    expect(screen.getByLabelText(/Extract timestamp/).tagName).toBe("BUTTON");
  });
```

(Add these two `it` blocks inside the existing `describe("WorkflowInputForm", ...)` block, after the last existing test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/composer/workflow-input-form.test.tsx`
Expected: FAIL — `date`/`datetime` currently fall into the plain-`<Input>` branch, so `screen.getByLabelText(...).tagName` is `"INPUT"`, not `"BUTTON"`.

- [ ] **Step 3: Update the implementation**

In `frontend/components/composer/workflow-input-form.tsx`, add the import:

```tsx
import { DatePickerInput, DateTimePickerInput } from "./date-field";
```

Update the field-rendering branch (lines 81–102):

```tsx
              {f.type === "json" ? (
                <Textarea
                  id={`f-${f.name}`}
                  rows={6}
                  {...form.register(f.name)}
                  placeholder='{"example": "value"}'
                  className="font-mono text-xs"
                />
              ) : f.type === "document" ? (
                <DocumentField
                  name={f.name}
                  required={f.required}
                  setValue={(v) => form.setValue(f.name, v)}
                  registered={form.register(f.name)}
                />
              ) : f.type === "date" ? (
                <DatePickerInput
                  name={f.name}
                  value={(form.watch(f.name) as string) ?? ""}
                  setValue={(v) => form.setValue(f.name, v, { shouldValidate: true })}
                  registered={form.register(f.name)}
                />
              ) : f.type === "datetime" ? (
                <DateTimePickerInput
                  name={f.name}
                  value={(form.watch(f.name) as string) ?? ""}
                  setValue={(v) => form.setValue(f.name, v, { shouldValidate: true })}
                  registered={form.register(f.name)}
                />
              ) : (
                <Input
                  id={`f-${f.name}`}
                  type={f.type === "number" ? "number" : f.type === "boolean" ? "checkbox" : "text"}
                  {...form.register(f.name)}
                />
              )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/composer/workflow-input-form.test.tsx`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/composer/workflow-input-form.tsx frontend/components/composer/workflow-input-form.test.tsx
git commit -m "$(cat <<'EOF'
feat: render calendar pickers for date/datetime Start inputs in the run form

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: "Run draft" dialog (`run-draft-dialog.tsx`)

**Files:**
- Modify: `frontend/components/composer/canvas/run-draft-dialog.tsx:179-192`
- Modify (add test): `frontend/components/composer/canvas/run-draft-dialog.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/components/composer/canvas/run-draft-dialog.test.tsx`, inside the existing `describe("RunDraftDialog", ...)` block:

```tsx
  it("renders a date picker trigger with a seeded default value for a date-typed Start input", () => {
    const workflow = {
      nodes: [
        {
          type: "start",
          data: {
            inputVariables: [
              {
                name: "report_date",
                type: "date",
                required: true,
                description: "Report date",
                defaultValue: "2026-07-20",
              },
            ],
          },
        },
      ],
    };
    render(
      wrap(
        <RunDraftDialog
          open={true}
          onOpenChange={vi.fn()}
          workflowId="wf1"
          workflow={workflow}
          onStarted={vi.fn()}
        />
      )
    );
    expect(screen.getByText("Jul 20, 2026")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/composer/canvas/run-draft-dialog.test.tsx`
Expected: FAIL — the field currently renders as a plain `<Input type="text">` showing the raw string in its `value` attribute, not as "Jul 20, 2026" display text

- [ ] **Step 3: Update the implementation**

In `frontend/components/composer/canvas/run-draft-dialog.tsx`, add the import:

```tsx
import { DatePickerInput, DateTimePickerInput } from "../date-field";
```

Update the field-rendering branch (lines 163–192), adding `date`/`datetime` cases before the final `<Input>` fallback:

```tsx
              {f.type === "json" ? (
                <Textarea
                  id={`draft-${f.name}`}
                  rows={5}
                  {...form.register(f.name)}
                  placeholder='{"example": "value"}'
                  className="font-mono text-xs"
                />
              ) : f.type === "document" ? (
                <DocumentField
                  name={f.name}
                  required={f.required}
                  setValue={(v) => form.setValue(f.name, v)}
                  registered={form.register(f.name)}
                  idPrefix="draft"
                />
              ) : f.type === "boolean" ? (
                <input
                  id={`draft-${f.name}`}
                  type="checkbox"
                  {...form.register(f.name)}
                  className="h-4 w-4"
                />
              ) : f.type === "date" ? (
                <DatePickerInput
                  name={f.name}
                  value={(form.watch(f.name) as string) ?? ""}
                  setValue={(v) => form.setValue(f.name, v, { shouldValidate: true })}
                  registered={form.register(f.name)}
                  idPrefix="draft"
                />
              ) : f.type === "datetime" ? (
                <DateTimePickerInput
                  name={f.name}
                  value={(form.watch(f.name) as string) ?? ""}
                  setValue={(v) => form.setValue(f.name, v, { shouldValidate: true })}
                  registered={form.register(f.name)}
                  idPrefix="draft"
                />
              ) : (
                <Input
                  id={`draft-${f.name}`}
                  type={f.type === "number" ? "number" : "text"}
                  {...form.register(f.name)}
                />
              )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/composer/canvas/run-draft-dialog.test.tsx`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/composer/canvas/run-draft-dialog.tsx frontend/components/composer/canvas/run-draft-dialog.test.tsx
git commit -m "$(cat <<'EOF'
feat: render calendar pickers for date/datetime Start inputs in the run-draft dialog

defaultValues seeding already coerces any non-string default to
String(f.default) for unrecognized types, so date/datetime defaults
flow through with no new coercion case needed.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Playwright e2e — end-user picks a date

**Files:**
- Create: `frontend/e2e/date-field.spec.ts`

- [ ] **Step 1: Write the test**

```ts
// frontend/e2e/date-field.spec.ts
/**
 * date-field.spec.ts — Start-node "date" field type: calendar picker
 * end-to-end.
 *
 * Publishes a workflow whose Start node declares a `report_date` field
 * of type "date", drives the calendar picker in the run form, and
 * confirms the started execution's submitted input carries the plain
 * ISO date string the picker produced.
 *
 * Requires: uvicorn + next dev running (or CI webServer config).
 */

import { test, expect, request as playwrightRequest } from "@playwright/test";
import { createTestUser } from "./fixtures/test-user";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

const DATE_FIELD_WORKFLOW_BODY = {
  name: "PW Date Field Workflow",
  description: "Created by Playwright date-field spec",
  nodes: [
    {
      id: "s",
      type: "start",
      position: { x: 0, y: 0 },
      data: {
        label: "Start",
        inputVariables: [
          { name: "report_date", type: "date", required: true, description: "Report date" },
        ],
      },
    },
    { id: "e", type: "end", position: { x: 400, y: 0 }, data: { label: "End" } },
  ],
  edges: [{ id: "e1", source: "s", target: "e" }],
  isTemplate: false,
  isPublic: true,
  isProduction: true,
  externalSlug: null,
};

async function createAndPublishWorkflow(accessToken: string): Promise<string> {
  const ctx = await playwrightRequest.newContext();
  const res = await ctx.post(`${apiUrl}/workflows`, {
    data: DATE_FIELD_WORKFLOW_BODY,
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (res.status() !== 201) {
    throw new Error(`createWorkflow failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { id: string };
  await ctx.dispose();
  return body.id;
}

function todayISO(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function todayLongLabel(): string {
  return new Date().toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

function todayShortLabel(): string {
  return new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

test("end-user picks a date via the calendar and it's submitted as plain ISO text", async ({
  page,
}) => {
  const user = await createTestUser();
  const workflowId = await createAndPublishWorkflow(user.accessToken);

  await page.goto("/login");
  await page.fill('input[type="email"]', user.email);
  await page.fill('input[type="password"]', user.password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/runs/, { timeout: 15_000 });

  await page.goto(`/runs/${workflowId}`);
  await expect(page.locator("h2")).toContainText("PW Date Field Workflow", { timeout: 10_000 });

  // Open the calendar and pick today.
  await page.locator("#f-report_date").click();
  await page.getByRole("button", { name: todayLongLabel() }).click();

  // The trigger now shows the picked date.
  await expect(page.locator("#f-report_date")).toContainText(todayShortLabel());

  await page.getByRole("button", { name: /run|submit/i }).click();
  await expect(page).toHaveURL(/\/runs\/.+\/executions\/.+/, { timeout: 15_000 });

  const executionId = page.url().split("/executions/")[1];
  const ctx = await playwrightRequest.newContext();
  const res = await ctx.get(`${apiUrl}/executions/${executionId}`, {
    headers: { Authorization: `Bearer ${user.accessToken}` },
  });
  const execution = (await res.json()) as { input: Record<string, unknown> };
  expect(execution.input.report_date).toBe(todayISO());
  await ctx.dispose();
});
```

- [ ] **Step 2: Run the spec (requires running backend + frontend dev servers)**

Run: `cd frontend && npx playwright test e2e/date-field.spec.ts`
Expected: PASS. If the backend/frontend dev servers aren't already running, start them first per the repo's existing Playwright setup (see how `end-user.spec.ts`/`designer.spec.ts` are run in CI — same prerequisites apply here, no new setup needed).

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/date-field.spec.ts
git commit -m "$(cat <<'EOF'
test: add Playwright e2e coverage for the Start-node date field

Publishes a workflow with a date-typed Start input, drives the
calendar picker for real in a browser, and confirms the execution's
submitted input is the plain ISO date string — the one interaction
(Base UI Popover open/close) intentionally not covered by jsdom
component tests.

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Type-check the whole frontend**

Run: `cd frontend && npm run type-check`
Expected: no errors

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint`
Expected: no errors

- [ ] **Step 3: Format check**

Run: `cd frontend && npm run format:check`
Expected: no errors. If it fails, run `npm run format` and re-check.

- [ ] **Step 4: Full Vitest suite**

Run: `cd frontend && npm test`
Expected: all tests pass (no regressions in unrelated files)

- [ ] **Step 5: Manual smoke check**

Run: `cd frontend && npm run dev` (with the backend running per the repo's usual dev setup), then in a browser:
1. Open the Designer for any workflow, select the Start node.
2. Add a variable, set its type to "date" — confirm the calendar pops up on clicking the default-value trigger and picking a day writes a `YYYY-MM-DD` string.
3. Set another variable's type to "date & time" — confirm the calendar + time input both work and combine into a `YYYY-MM-DDTHH:mm:ss` string.
4. Save the workflow, open "Run draft" from the canvas, confirm both pickers render with any seeded default values shown correctly.
5. Publish and run from the production run form (`/runs/{id}`), confirm the same pickers work there.

- [ ] **Step 6: Update CHANGELOG.md** (if the repo convention is to log user-facing features there — check `CHANGELOG.md`'s existing entries for the right format/section before adding one)

- [ ] **Step 7: Final commit** (only if Steps 3 or 6 produced changes)

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore: format fixes + changelog entry for Start-node date field

Co-Authored-By: Balaji Rajan <balajirajan@gmail.com>
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```
