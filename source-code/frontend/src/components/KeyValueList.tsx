import type { ReactNode } from "react";

export interface KeyValue {
  label: string;
  value: ReactNode;
  /** Shown beside the value: a TruthBadge, a FreshnessBadge. */
  extra?: ReactNode;
}

/** Labelled values: `dl` semantics; an unknown value says so and is never rendered as zero. */
export function KeyValueList({ items, dense = false }: { items: readonly KeyValue[]; dense?: boolean }) {
  return (
    <dl className={`kv${dense ? " kv-dense" : ""}`}>
      {items.map((item) => (
        <div className="kv-row" key={item.label}>
          <dt>{item.label}</dt>
          <dd>
            <span className="kv-value">{item.value}</span>
            {item.extra ? <span className="kv-extra">{item.extra}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}
