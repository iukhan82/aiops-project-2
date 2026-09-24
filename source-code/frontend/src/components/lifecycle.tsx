import type { ReactNode } from "react";
import { utcClock } from "../lib/format";
import { Button } from "./Button";
import { TruthBadge } from "./badges";
import { Shape } from "./Shape";

export interface Step {
  id: string;
  label: string;
  state: "done" | "current" | "upcoming" | "unavailable";
  /** A legal next step is a control; the rest are labels. */
  onSelect?: () => void;
  busy?: boolean;
}

const STEP_SHAPE = { done: "check", current: "diamond", upcoming: "ring", unavailable: "dash" } as const;
const STEP_WORD = { done: "done", current: "current", upcoming: "not yet", unavailable: "not available" } as const;

/** A lifecycle position. Done, current and upcoming are told apart by shape and word, not colour; only legal next steps are controls. */
export function Stepper({ label, steps }: { label: string; steps: readonly Step[] }) {
  return (
    <ol className="stepper" aria-label={label}>
      {steps.map((step) => (
        <li key={step.id} className={`step step-${step.state}`} aria-current={step.state === "current" ? "step" : undefined}>
          <Shape name={STEP_SHAPE[step.state]} size={14} />
          <span className="step-label">{step.label}</span>
          <span className="step-state">{STEP_WORD[step.state]}</span>
          {step.onSelect ? (
            <Button variant="secondary" onClick={step.onSelect} busy={step.busy}>
              Record {step.label.toLowerCase()}
            </Button>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

export interface TimelineEntry {
  id: string;
  at: string;
  source?: string;
  text: ReactNode;
  truth?: string | null;
}

/** An append-only sequence: UTC time, source, description and truth label. Oldest first, and it says so. Never truncated. */
export function Timeline({ entries, label, empty = "Nothing recorded yet." }: { entries: readonly TimelineEntry[]; label: string; empty?: string }) {
  if (entries.length === 0) return <p className="muted">{empty}</p>;
  return (
    <>
      <p className="muted timeline-order">Oldest first.</p>
      <ol className="timeline" aria-label={label}>
        {entries.map((entry) => (
          <li key={entry.id}>
            <time dateTime={entry.at}>{utcClock(entry.at)}</time>
            <div className="timeline-body">
              {entry.source ? <span className="timeline-source">{entry.source}</span> : null}
              <span>{entry.text}</span>
              {entry.truth ? <TruthBadge label={entry.truth} /> : null}
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

/** "Load more" for cursor lists (the API has no page numbers). Announces how many arrived. */
export function LoadMore({ shown, hasMore, loading, onMore, noun }: { shown: number; hasMore: boolean; loading: boolean; onMore: () => void; noun: string }) {
  return (
    <div className="load-more">
      <span role="status" className="muted">
        {shown} {noun} shown{hasMore ? "; more available." : "; that is all of them."}
      </span>
      {hasMore ? (
        <Button variant="secondary" onClick={onMore} busy={loading}>
          Load more
        </Button>
      ) : null}
    </div>
  );
}
