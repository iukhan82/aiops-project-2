import statusJson from "../design/status.json";

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";
export type ShapeName = "circle" | "ring" | "triangle" | "square" | "diamond" | "cross" | "dash" | "half" | "pause" | "check" | "arrow-left" | "hexagon";

export interface StatusEntry {
  label: string;
  shape: ShapeName;
  tone: Tone;
  meaning: string;
}

const domains = statusJson.domains as unknown as Record<string, Record<string, StatusEntry>>;

export type Domain = keyof typeof statusJson.domains;

export function statusEntry(domain: Domain, value: string | null | undefined): StatusEntry {
  const entry = value ? domains[domain]?.[value] : undefined;
  if (entry) return entry;
  if (value) console.warn(`Unknown ${domain} status "${value}" shown as received`);
  return { label: value ?? "Not available", shape: "ring", tone: "neutral", meaning: `A ${domain} state this screen does not recognise. Shown as received, not hidden.` };
}

export function isKnownStatus(domain: Domain, value: string): boolean {
  return Boolean(domains[domain]?.[value]);
}

export const STATUS_DOMAINS = Object.keys(domains);
