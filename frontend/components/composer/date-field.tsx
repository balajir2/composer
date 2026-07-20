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
          <Calendar
            value={date}
            onChange={(d) => onChange(formatDateTimeISO(d, time || "00:00"))}
          />
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
