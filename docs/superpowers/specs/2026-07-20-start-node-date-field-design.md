# Design: Date / DateTime field types for the Start node

**Date:** 2026-07-20
**Status:** Approved (design phase) — implementation plan not yet written
**Origin:** User request while working a customer's "Jira-Driven Weekly Tracking" workflow, which
needs a `report_date` (date-only) and `extract_timestamp` (date+time) input on its Start node.
Purely a Designer/runtime UX improvement — not OAB-parity work, does not touch
`D:/GitHub/open-agent-builder`.

## Why this spec exists

Start-node input fields (`src/engine/workflow.py`'s `StartInputVariable`,
`frontend/lib/start-node-schema.ts`'s `StartField`) currently support `text | number | boolean |
json | document`. A workflow author who needs a date has to tell every end user to type
`2026-07-20` correctly by hand, in both the Designer panel (setting a field's default value) and
the "run workflow" form (the actual value a user submits at execution time). This spec adds `date`
and `datetime` as two new field types, each rendering a calendar picker instead of a plain text
box, wherever Start-node field values are entered.

## Decisions locked by user Q&A during brainstorming

- **Text is the only wire format — no engine changes.** `StartInputVariable.type` is already a
  free-form `str` on the backend (not a `Literal`/enum); the actual submitted value is, and
  remains, plain text. `date`/`datetime` are new *accepted values* for that string field, used
  purely as a frontend rendering hint. The workflow engine, execution payload, and every executor
  downstream see exactly what they see today for a `text` field — a string. This was an explicit
  user steer against over-engineering: no new backend validation, no per-field format
  configuration, no engine-side date parsing.
- **Two new types, not one.** `report_date` wants date-only; `extract_timestamp` wants date+time.
  Rather than force a time-of-day onto every date field (or bolt an optional time toggle onto a
  single type), these are two distinct field types: `date` and `datetime`.
- **Fixed serialization format, not configurable per field:**
  - `date` → `YYYY-MM-DD` (ISO 8601 date), e.g. `2026-07-20`.
  - `datetime` → `YYYY-MM-DDTHH:mm:ss` (ISO 8601, local time, no UTC offset), e.g.
    `2026-07-20T14:30:00`.
  - No per-field "format" setting in the Designer panel. If a future workflow needs a different
    text shape, that's a new ask, not something this feature tries to anticipate.
- **Calendar widget is hand-rolled, not a new dependency.** The repo has no date-picker library
  (`react-day-picker`, `date-fns`) and its shadcn-derived UI kit is built on **Base UI**
  (`@base-ui/react`), not Radix — the two libraries shadcn's official `Calendar` recipe assumes.
  Rather than pull in a library built for a different primitives stack, the calendar is a small
  hand-rolled month-grid component, following the same file-per-primitive convention already used
  for `dropdown-menu.tsx` etc.
- **Time selection reuses the native `<input type="time">`.** Building a custom time-of-day picker
  (hour/minute scroll list) was judged unnecessary scope for what's fundamentally "let the user
  avoid mistyping a date." The `datetime` picker's popover shows the custom calendar grid for the
  date portion (the part the user explicitly asked for: "a small calendar that pops up") plus a
  native time input beneath it for the time portion.
- **Same picker in both the Designer panel's default-value field and the runtime input forms.**
  Consistent experience — a workflow author configuring a default value for a `date` field gets
  the identical calendar picker an end user sees when actually running the workflow.

## Components

### New primitives (`frontend/components/ui/`)

- **`popover.tsx`** — thin wrapper over `@base-ui/react/popover`, following the same
  Root/Trigger/Portal/Positioner/Popup wrapping pattern already used in `dropdown-menu.tsx`
  (`Popover`, `PopoverTrigger`, `PopoverContent`). First Popover primitive in the repo; every other
  consumer of positioned floating content (dropdown menu, select, dialog) already exists — this
  fills the one gap.
- **`calendar.tsx`** — hand-rolled month-grid: header with month/year label and prev/next buttons
  (`ChevronLeft`/`ChevronRight` from `lucide-react`, already a dependency), a 7-column day grid,
  selected-day highlighting. Pure presentational component: `{ value?: Date; onChange: (d: Date)
  => void }`. No new dependency.

### New composed field (`frontend/components/composer/date-field.tsx`)

- **`DatePickerInput`** — trigger button showing the formatted date (e.g. "Jul 20, 2026") +
  `Popover` + `Calendar`. On day click, formats to `YYYY-MM-DD` and calls `onChange` with that
  string.
- **`DateTimePickerInput`** — same trigger/popover shell; popover body stacks `Calendar` above a
  native `<input type="time">`. On change to either sub-part, recombines both into
  `YYYY-MM-DDTHH:mm:ss` and calls `onChange` with that string.
- Both components take/return plain strings (not `Date` objects) at their boundary, so callers
  treat them exactly like a text `<Input>` — `value: string`, `onChange: (value: string) => void`.

### New formatting helpers (`frontend/lib/date-format.ts`)

- `formatDateISO(date: Date): string` → `YYYY-MM-DD`.
- `formatDateTimeISO(date: Date, time: string): string` → `YYYY-MM-DDTHH:mm:ss`.
- `parseISODate(text: string): Date | undefined` — used to seed the calendar's displayed
  month/selected-day when opening a picker that already has a value (editing an existing field's
  default value, or re-opening a run-draft dialog with a prefilled input).
- Pure functions, no dependency — plain `Date` arithmetic with manual zero-padding.

## Wiring — three consuming surfaces

1. **`frontend/lib/start-node-schema.ts`** — add `"date" | "datetime"` to the `StartField.type`
   union (currently `text | number | json | boolean | document` at line 10). In the zod-building
   switch (lines 87–124), both new types fall into the same branch as `"text"` — a plain string
   schema, required/optional exactly as `text` fields already behave. No new validation branch.
2. **`frontend/components/composer/canvas/node-panels/start.tsx`** (Designer panel) — add
   `"date"`/`"datetime"` to the `InputField.type` union (line 18) and to `TYPE_OPTIONS` (lines
   26–32) as "Date" / "Date & Time". In the default-value rendering branch (lines 138–173), render
   `DatePickerInput`/`DateTimePickerInput` in place of the plain `<Input>` when `field.type` is one
   of the new types; the picker writes its formatted string into `defaultValue` exactly as the
   plain input does today.
3. **`frontend/components/composer/workflow-input-form.tsx`** (production "run workflow" form) and
   **`frontend/components/composer/canvas/run-draft-dialog.tsx`** (run-from-canvas dialog) — both
   add a render branch for `date`/`datetime` alongside the existing `text | number | boolean |
   json | document` branches (`workflow-input-form.tsx` lines 81–102), using the same picker
   components. The underlying form value is the ISO string the whole way through — submission,
   `run-draft-dialog.tsx`'s `defaultValues` construction (lines 54–60), and the execution payload
   are all unchanged from how a `text` field behaves today; no new coercion case needed there.

## No backend changes

`StartInputVariable` (`src/engine/workflow.py:38`) needs no change — `type: str` already accepts
any string, and `default_value: Any | None` already accepts a plain string. `date`/`datetime` are
new values that string happens to hold; nothing in the Pydantic schema, execution payload
handling, or any executor reads or interprets `type` beyond passing it through to the frontend.

## Testing

- **`date-format.ts` unit tests** (Vitest, matching existing frontend test conventions):
  round-trip `formatDateISO`/`parseISODate`, correct zero-padding for single-digit
  month/day/hour/minute, `formatDateTimeISO` combining a date and a `"HH:mm"` time string
  correctly.
- **`calendar.tsx` / `date-field.tsx` component tests**: month navigation, day selection calls
  `onChange` with the right value, `datetime` variant recombines date+time edits correctly.
- **`start.tsx` panel test**: selecting "Date"/"Date & Time" from the type dropdown switches the
  default-value editor to the picker; picking a date writes the expected ISO string into the
  field's `defaultValue`.
- **`start-node-schema.ts` test**: `date`/`datetime` fields validate as required/optional strings
  the same way `text` fields do.
- **Playwright e2e**: one flow — add a `date` field to a Start node, run the workflow, pick a date
  via the calendar in the run form, confirm the execution's submitted input payload contains the
  plain `YYYY-MM-DD` string.

## Explicitly deferred / out of scope

- Per-field configurable date/datetime text format.
- Timezone-aware `datetime` (UTC offset, timezone selection) — local-time-only string, matching
  the "don't over-engineer" steer.
- A custom (non-native) time-of-day picker.
- Date *range* selection (start/end pair) — not requested; each field is a single value.
- Any change to how `StartInputVariable`/`StartNodeData` are validated or stored server-side.
