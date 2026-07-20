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
