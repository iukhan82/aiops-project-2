/** One fixed format for every number and time, independent of the browser locale (docs/ux/DESIGN_SYSTEM.md). */

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

export function parseTime(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function utcClock(iso: string | null | undefined): string {
  const date = parseTime(iso);
  return date ? `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())} UTC` : "not available";
}

export function utcDateTime(iso: string | null | undefined): string {
  const date = parseTime(iso);
  if (!date) return "not available";
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${utcClock(iso)}`;
}

export function ageSeconds(iso: string | null | undefined, now: number = Date.now()): number | null {
  const date = parseTime(iso);
  return date ? Math.max(0, Math.round((now - date.getTime()) / 1000)) : null;
}

export function ageText(seconds: number | null): string {
  if (seconds === null) return "age not available";
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${pad(seconds % 60)} s ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ${pad(Math.floor((seconds % 3600) / 60))} min ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

export function number(value: number | null | undefined, digits = 0, unit = ""): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "not available";
  const fixed = value.toFixed(digits);
  const [whole = "", fraction] = fixed.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${grouped}${fraction !== undefined ? `.${fraction}` : ""}${unit ? ` ${unit}` : ""}`;
}

export function percent(fraction: number | null | undefined, digits = 0): string {
  return fraction === null || fraction === undefined ? "not available" : `${(fraction * 100).toFixed(digits)} %`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "not available";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min ${pad(s % 60)} s`;
}

export function words(code: string): string {
  const text = code.replace(/[_.-]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}
