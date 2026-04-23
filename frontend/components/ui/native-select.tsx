"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A simple native `<select>` styled to match the shadcn design system.
 *
 * Replaces the base-ui shadcn `Select` primitive (which proved unreliable
 * for basic click-to-select interactions) everywhere a dropdown of string
 * values is needed in node property panels.  Fully keyboard-accessible
 * and touch-friendly with zero extra JS.
 */
export interface NativeSelectOption {
  value: string;
  label: string;
}

export interface NativeSelectProps
  extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "onChange"> {
  value: string | undefined;
  onValueChange: (value: string) => void;
  options: NativeSelectOption[];
  placeholder?: string;
}

export const NativeSelect = React.forwardRef<HTMLSelectElement, NativeSelectProps>(
  function NativeSelect(
    { className, value, onValueChange, options, placeholder, disabled, ...rest },
    ref
  ) {
    return (
      <select
        ref={ref}
        value={value ?? ""}
        onChange={(e) => onValueChange(e.target.value)}
        disabled={disabled}
        className={cn(
          "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        {...rest}
      >
        {placeholder && (
          <option value="" disabled hidden>
            {placeholder}
          </option>
        )}
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    );
  }
);
