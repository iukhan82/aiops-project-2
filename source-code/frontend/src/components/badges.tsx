import { ageSeconds, ageText, parseTime, utcClock } from "../lib/format";
import { statusEntry, type Domain } from "../lib/status";
import { useNow } from "./Clock";
import { Shape } from "./Shape";

/** A backend state as shape + word + tone. Unknown values render as received, never hidden. */
export function StatusChip({ domain, value, detail }: { domain: Domain; value: string | null | undefined; detail?: string }) {
  const entry = statusEntry(domain, value);
  return (
    <span className={`chip tone-${entry.tone}`} title={entry.meaning} data-status={value ?? "none"}>
      <Shape name={entry.shape} />
      <span>{entry.label}</span>
      {detail ? <span className="chip-detail">{detail}</span> : null}
    </span>
  );
}

export function TruthBadge({ label }: { label: string | null | undefined }) {
  const entry = statusEntry("truth", label);
  return (
    <span className={`chip chip-small tone-${entry.tone}`} title={entry.meaning} data-truth={label ?? "none"}>
      <Shape name={entry.shape} size={10} />
      <span>{entry.label}</span>
    </span>
  );
}

export type Freshness = "fresh" | "stale" | "unknown";

export function freshnessOf(observedAt: string | null | undefined, staleAfterSeconds: number, now: number): Freshness {
  const age = ageSeconds(observedAt, now);
  if (age === null) return "unknown";
  return age > staleAfterSeconds ? "stale" : "fresh";
}

/** Fresh, stale or unknown, with the observation time and its age. The age is text, not a live region. */
export function FreshnessBadge({ observedAt, staleAfterSeconds = 120, status }: { observedAt: string | null | undefined; staleAfterSeconds?: number; status?: Freshness }) {
  const now = useNow();
  // `status` is the server's own judgement (it saw the samples); without one it is worked out from the observation time.
  // A "fresh" verdict can go out of date between refreshes, so it is also checked against the clock; it can only get worse.
  const local = freshnessOf(observedAt, staleAfterSeconds, now);
  const freshness: Freshness = status === undefined ? local : status === "fresh" && local === "stale" ? "stale" : status;
  const entry = statusEntry("freshness", freshness);
  const when = parseTime(observedAt);
  return (
    <span className={`chip chip-small tone-${entry.tone}`} data-freshness={freshness} title={when ? when.toISOString() : "No observation time"}>
      <Shape name={entry.shape} size={10} />
      <span>
        {entry.label}
        {when ? `, observed ${utcClock(observedAt)}, ${ageText(ageSeconds(observedAt, now))}` : ", no observation time"}
      </span>
    </span>
  );
}
