import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { Outcome } from "../api/types";
import { useApi } from "../api/useApi";
import { useCursorList } from "../api/useCursorList";
import { Banner } from "../components/Banner";
import { DataTable, type Column } from "../components/DataTable";
import { KeyValueList } from "../components/KeyValueList";
import { Page, Panel } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip, TruthBadge } from "../components/badges";
import { Select } from "../components/controls";
import { LoadMore } from "../components/lifecycle";
import { number, utcDateTime, words } from "../lib/format";

interface Detail {
  outcome: Outcome;
  detail: Record<string, unknown> & { undo?: Record<string, unknown> | null; noise?: { threshold_s?: number; rule?: string }; thresholds?: { safety_regression?: number; effectiveness_improvement?: number } };
}

const undoText = (undo: Record<string, unknown>) => {
  const parts = [undo.kind, undo.mode].filter((v) => typeof v === "string" && v).map((v) => words(String(v)));
  const detail = [undo.note, undo.evidence].find((v) => typeof v === "string" && v);
  return `${parts.join(", ") || "Undo"}${detail ? `: ${String(detail)}` : ""}`;
};

const summary = (o: Outcome, side: "pre_window" | "post_window") => o[side].measurements.map((m) => `${words(m.name)} ${number(m.value, 1, m.unit)}`).join("; ") || "no measurement";

export function OutcomesPage() {
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);
  const list = useCursorList<Outcome>("/api/v1/outcomes", filter === "all" ? {} : { classification: filter });
  const detail = useApi<Detail>(selected ? `/api/v1/outcomes/${selected}` : null);
  const reload = list.reload;
  const refresh = useCallback(() => reload(), [reload]);
  const rows = useMemo(() => list.items, [list.items]);

  const columns: Column<Outcome>[] = [
    {
      key: "class",
      header: "Outcome",
      sortValue: (o) => o.classification,
      render: (o) => (
        <button type="button" className="link-button" aria-pressed={selected === o.outcome_id} onClick={() => setSelected(o.outcome_id)}>
          <StatusChip domain="outcome" value={o.classification} />
        </button>
      ),
    },
    { key: "before", header: "Before", render: (o) => summary(o, "pre_window") },
    { key: "after", header: "After", render: (o) => summary(o, "post_window") },
    { key: "rolled", header: "Rolled back", sortValue: (o) => String(o.rollback_triggered), render: (o) => (o.rollback_triggered ? "Yes, undo confirmed" : "No") },
    { key: "verified", header: "Verified", sortValue: (o) => o.verified_at, render: (o) => utcDateTime(o.verified_at) },
    { key: "by", header: "Verifier", render: (o) => o.verifier },
    { key: "command", header: "Command", render: (o) => <Link to={`/actions/commands/${o.command_id}`}>open</Link> },
  ];

  const d = detail.data;
  return (
    <Page title="Verified outcomes" subtitle="What each executed command actually did, measured before and after by a verifier that had no part in it. Effective, ineffective, unsafe (rolled back) or unknown (escalated).">
      {list.error ? <Banner tone="warn">The list could not be refreshed.</Banner> : null}
      <div className="filters">
        <Select
          label="Outcome"
          value={filter}
          onChange={(value) => {
            setFilter(value);
            setSelected(null);
          }}
          options={[
            { value: "all", label: "All outcomes" },
            { value: "effective", label: "Effective" },
            { value: "ineffective", label: "Ineffective" },
            { value: "unsafe", label: "Unsafe (rolled back)" },
            { value: "unknown", label: "Unknown (escalated)" },
          ]}
        />
      </div>
      <div className="two-col">
        <div>
          {list.loading ? (
            <LoadingState what="outcomes" />
          ) : list.error && list.items.length === 0 ? (
            <ErrorState what="The outcomes" error={list.error} onRetry={refresh} />
          ) : (
            <>
              <p role="status" className="muted">
                {rows.length} {rows.length === 1 ? "outcome" : "outcomes"} shown.
              </p>
              <DataTable caption="Verified outcomes" columns={columns} rows={rows} rowKey={(o) => o.outcome_id} selectedKey={selected} defaultSort={{ key: "verified", direction: "desc" }} empty={<EmptyState title="No outcome">A command is verified after it has executed and its after-window has closed.</EmptyState>} />
              <LoadMore shown={list.items.length} hasMore={list.hasMore} loading={list.loadingMore} onMore={list.loadMore} noun="outcomes loaded" />
            </>
          )}
        </div>
        <Panel title="Selected outcome" id="outcome-detail">
          {!selected ? (
            <p className="muted">Select an outcome to see what was measured, against which thresholds, and how any rollback was confirmed.</p>
          ) : detail.loading ? (
            <LoadingState what="the outcome" />
          ) : detail.error && !d ? (
            <ErrorState what="The outcome" error={detail.error} onRetry={detail.reload} />
          ) : d ? (
            <>
              <p>
                <StatusChip domain="outcome" value={d.outcome.classification} /> <TruthBadge label="verified" />
              </p>
              <KeyValueList
                items={[
                  { label: "Before window", value: `${utcDateTime(d.outcome.pre_window.from)} to ${utcDateTime(d.outcome.pre_window.to)}` },
                  { label: "Before", value: summary(d.outcome, "pre_window") },
                  { label: "After window", value: `${utcDateTime(d.outcome.post_window.from)} to ${utcDateTime(d.outcome.post_window.to)}` },
                  { label: "After", value: summary(d.outcome, "post_window") },
                  ...(d.detail.classification_reason ? [{ label: "Why", value: String(d.detail.classification_reason) }] : []),
                  ...(d.detail.thresholds ? [{ label: "Thresholds", value: `safety regression ${d.detail.thresholds.safety_regression}, effectiveness ${d.detail.thresholds.effectiveness_improvement}` }] : []),
                  ...(d.detail.noise ? [{ label: "Noise band rule", value: `${d.detail.noise.rule ?? ""} (threshold ${d.detail.noise.threshold_s ?? "?"} s)` }] : []),
                  { label: "Verified by", value: d.outcome.verifier },
                  { label: "Rolled back", value: d.outcome.rollback_triggered ? "Yes" : "No" },
                  ...(d.outcome.escalation_reason ? [{ label: "Escalated because", value: d.outcome.escalation_reason }] : []),
                  ...(d.detail.undo ? [{ label: "Physical undo", value: undoText(d.detail.undo) }] : []),
                  ...(d.detail.basis ? [{ label: "Basis", value: String(d.detail.basis) }] : []),
                  ...(d.detail.limit ? [{ label: "Limit", value: String(d.detail.limit) }] : []),
                  ...(d.detail.not_executed_in_this_session ? [{ label: "Recorded", value: `Not executed in this session: ${String(d.detail.recorded_from)}` }] : []),
                ]}
              />
            </>
          ) : null}
        </Panel>
      </div>
    </Page>
  );
}
