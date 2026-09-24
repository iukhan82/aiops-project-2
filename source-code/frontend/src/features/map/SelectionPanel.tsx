import { Link } from "react-router-dom";
import { FreshnessBadge, StatusChip, TruthBadge } from "../../components/badges";
import { KeyValueList } from "../../components/KeyValueList";
import { utcDateTime } from "../../lib/format";
import { KIND_LABEL, type MapItem } from "./model";

/** The same set of facts for whatever is selected: value, unit, observed time, truth label, freshness. */
export function SelectionPanel({ item, staleAfterSeconds = 120 }: { item: MapItem | null; staleAfterSeconds?: number }) {
  if (!item) {
    return (
      <section className="panel" aria-labelledby="selection-heading" data-testid="selection-panel">
        <h2 id="selection-heading" tabIndex={-1}>
          Selection
        </h2>
        <p className="muted">Select a road segment, junction, device, incident or unit on the map or in the list to see its readings here.</p>
      </section>
    );
  }
  return (
    <section className="panel" aria-labelledby="selection-heading" data-testid="selection-panel" data-selected-id={item.id}>
      <h2 id="selection-heading" tabIndex={-1}>
          Selection
        </h2>
      <p className="selection-kind">{KIND_LABEL[item.kind]}</p>
      <h3 className="selection-name">{item.name}</h3>
      <KeyValueList
        items={[
          ...(item.status ? [{ label: "Status", value: <StatusChip domain={item.status.domain} value={item.status.value} /> }] : []),
          { label: "Reading", value: item.reading },
          ...(item.observedAt || item.freshness
            ? [
                {
                  label: "Observed",
                  value: item.observedAt ? utcDateTime(item.observedAt) : "not available",
                  extra: item.freshness ? <FreshnessBadge observedAt={item.observedAt} staleAfterSeconds={staleAfterSeconds} status={item.kind === "segment" ? (item.freshness ?? undefined) : undefined} /> : undefined,
                },
              ]
            : []),
          ...(item.truth ? [{ label: "Truth label", value: <TruthBadge label={item.truth} /> }] : []),
          ...item.details.map((d) => ({ label: d.label, value: d.value })),
        ]}
      />
      {item.href ? (
        <p className="selection-link">
          <Link to={item.href}>Open {KIND_LABEL[item.kind].toLowerCase()} details</Link>
        </p>
      ) : null}
    </section>
  );
}
