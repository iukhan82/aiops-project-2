import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useCursorList } from "../api/useCursorList";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { DataTable, type Column } from "../components/DataTable";
import { Page } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip } from "../components/badges";
import { Select, TextField } from "../components/controls";
import { LoadMore } from "../components/lifecycle";
import { AUDIT_SOURCE_TEXT, ENTITY_TYPE_TEXT, auditActionText, detailText, entityPath, type AuditRecord } from "../features/govern/model";
import { utcClock, utcDateTime, words } from "../lib/format";

interface Filters {
  actor: string;
  entityType: string;
  entityId: string;
  outcome: string;
  since: string;
  until: string;
}

const EMPTY: Filters = { actor: "", entityType: "", entityId: "", outcome: "", since: "", until: "" };

/** The time fields are UTC, like every time on these screens; the browser's own zone plays no part. */
const asUtc = (value: string) => (value ? `${value}:00Z` : undefined);

export function AuditPage() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [rangeError, setRangeError] = useState<string | null>(null);
  const query = useMemo(
    () => ({ actor: applied.actor.trim() || undefined, entity_type: applied.entityType || undefined, entity_id: applied.entityId.trim() || undefined, outcome: applied.outcome || undefined, since: asUtc(applied.since), until: asUtc(applied.until) }),
    [applied],
  );
  const list = useCursorList<AuditRecord>("/api/v1/audit", query);
  const filtered = Object.values(applied).some(Boolean);

  const apply = (event: FormEvent) => {
    event.preventDefault();
    if (draft.since && draft.until && draft.since > draft.until) {
      setRangeError("The start is after the end. Swap them, or clear one.");
      return;
    }
    setRangeError(null);
    setApplied(draft);
  };
  const clear = () => {
    setDraft(EMPTY);
    setApplied(EMPTY);
    setRangeError(null);
  };
  const onlyThisEntity = (record: AuditRecord) => {
    const next = { ...EMPTY, entityType: record.entity_type ?? "", entityId: record.entity_id ?? "" };
    setDraft(next);
    setApplied(next);
  };

  const columns: Column<AuditRecord>[] = [
    { key: "at", header: "Time", nowrap: true, sortValue: (r) => r.at, render: (r) => utcDateTime(r.at) },
    {
      key: "who",
      header: "Who",
      nowrap: true,
      sortValue: (r) => r.actor ?? "",
      render: (r) => (
        <>
          {r.actor ?? <span className="muted">not recorded</span>}
          {r.actor_roles.length > 0 ? <span className="cell-note">{r.actor_roles.map(words).join(", ")}</span> : null}
        </>
      ),
    },
    { key: "action", header: "What", sortValue: (r) => auditActionText(r), render: (r) => auditActionText(r) },
    {
      key: "entity",
      header: "Concerning",
      sortValue: (r) => `${r.entity_type ?? ""} ${r.entity_id ?? ""}`,
      render: (r) => {
        if (!r.entity_type) return <span className="muted">nothing specific</span>;
        const path = entityPath(r.entity_type, r.entity_id);
        const label = `${ENTITY_TYPE_TEXT[r.entity_type] ?? words(r.entity_type)}${r.entity_id ? ` ${r.entity_id.slice(0, 8)}` : ""}`;
        return (
          <>
            {path ? <Link to={path}>{label}</Link> : label}
            {r.entity_id ? (
              <button type="button" className="link-button cell-note" onClick={() => onlyThisEntity(r)} aria-label={`Show only ${label}`}>
                Only this
              </button>
            ) : null}
          </>
        );
      },
    },
    { key: "outcome", header: "Result", sortValue: (r) => r.outcome, render: (r) => <StatusChip domain="audit" value={r.outcome} /> },
    { key: "source", header: "Recorded in", sortValue: (r) => r.source, render: (r) => AUDIT_SOURCE_TEXT[r.source] ?? words(r.source) },
    { key: "detail", header: "Detail", render: (r) => detailText(r.detail) || <span className="muted">none</span> },
  ];

  return (
    <Page
      title="Audit trail"
      subtitle="Append-only. Everything a person or a service did, newest first, from the API and from each incident, command, call and assignment history. Nothing on this screen can be edited or deleted."
      actions={
        <Button variant="secondary" onClick={list.reload}>
          Refresh
        </Button>
      }
    >
      {list.error ? <Banner tone="warn">The trail could not be refreshed. What is shown was read {list.fetchedAt ? utcClock(new Date(list.fetchedAt).toISOString()) : "earlier"}.</Banner> : null}
      <form className="filters" onSubmit={apply} aria-label="Filter the audit trail">
        <TextField label="Who" value={draft.actor} onChange={(actor) => setDraft({ ...draft, actor })} type="search" hint="Part of a username." />
        <Select
          label="Concerning"
          value={draft.entityType}
          onChange={(entityType) => setDraft({ ...draft, entityType })}
          options={[{ value: "", label: "Anything" }, ...Object.entries(ENTITY_TYPE_TEXT).map(([value, label]) => ({ value, label: value === "endpoint" ? "Refused requests (endpoint)" : label }))]}
        />
        <TextField label="Identifier" value={draft.entityId} onChange={(entityId) => setDraft({ ...draft, entityId })} hint="The whole identifier." />
        <Select
          label="Result"
          value={draft.outcome}
          onChange={(outcome) => setDraft({ ...draft, outcome })}
          options={[
            { value: "", label: "Any result" },
            { value: "allowed", label: "Allowed" },
            { value: "denied", label: "Refused" },
            { value: "failed", label: "Failed" },
          ]}
        />
        <TextField label="From (UTC)" value={draft.since} onChange={(since) => setDraft({ ...draft, since })} type="datetime-local" />
        <TextField label="Until (UTC)" value={draft.until} onChange={(until) => setDraft({ ...draft, until })} type="datetime-local" error={rangeError} />
        <div className="filter-actions">
          <Button type="submit" variant="primary">
            Apply filters
          </Button>
          <Button variant="secondary" onClick={clear}>
            Clear filters
          </Button>
        </div>
      </form>
      {list.loading ? (
        <LoadingState what="the audit trail" />
      ) : list.error && list.items.length === 0 ? (
        <ErrorState what="The audit trail" error={list.error} onRetry={list.reload} />
      ) : (
        <>
          <p role="status" className="muted">
            {list.items.length} {list.items.length === 1 ? "record" : "records"} loaded{filtered ? " for these filters" : ""}
            {list.fetchedAt ? `, read ${utcClock(new Date(list.fetchedAt).toISOString())}` : ""}.
          </p>
          <DataTable
            caption="Audit trail"
            columns={columns}
            rows={list.items}
            rowKey={(r) => r.audit_id}
            defaultSort={{ key: "at", direction: "desc" }}
            empty={<EmptyState title="No record matches">{filtered ? "Nothing was recorded for these filters. Widen the time range or clear a filter." : "Nothing has been recorded yet."}</EmptyState>}
          />
          <LoadMore shown={list.items.length} hasMore={list.hasMore} loading={list.loadingMore} onMore={list.loadMore} noun="records" />
        </>
      )}
    </Page>
  );
}
