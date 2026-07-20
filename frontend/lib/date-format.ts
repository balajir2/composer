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
