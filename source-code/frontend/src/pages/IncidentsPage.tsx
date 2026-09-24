import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Incident } from "../api/types";
import { useCursorList } from "../api/useCursorList";
import { Banner } from "../components/Banner";
import { DataTable, type Column } from "../components/DataTable";
import { usePolledFeed } from "../components/Feed";
import { LiveRegion } from "../components/LiveRegion";
import { Page } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip, TruthBadge } from "../components/badges";
import { Select } from "../components/controls";
import { LoadMore } from "../components/lifecycle";
import { ACTIVE_INCIDENT } from "../features/map/geometry";
import { desk } from "../features/incidents/lifecycle";
import { number, utcClock, words } from "../lib/format";

const SEVERITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };
const REFRESH_MS = 10_000;

export function IncidentsPage() {
  const [paused, setPaused] = useState(false);
  const [statusFilter, setStatusFilter] = useState("active");
  const [typeFilter, setTypeFilter] = useState("all");
  const list = useCursorList<Incident>("/api/v1/incidents", {}, paused ? undefined : REFRESH_MS);
  const [announce, setAnnounce] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const known = useRef<Set<string> | null>(null);

  const togglePause = useCallback(() => setPaused((p) => !p), []);
  const newest = list.items.reduce<string | null>((latest, i) => (!latest || i.updated_at > latest ? i.updated_at : latest), null);
  usePolledFeed([list], newest, paused, togglePause);

  useEffect(() => {
    if (list.items.length === 0 && known.current === null) return;
    const ids = new Set(list.items.map((i) => i.incident_id));
    if (known.current) {
      const fresh = list.items.filter((i) => !known.current!.has(i.incident_id));
      if (fresh.length > 0) {
        setAnnounce((current) => [...fresh.map((i) => `${utcClock(i.opened_at)} new ${i.severity} ${words(i.incident_type)} at ${i.network_element_id}`), ...current].slice(0, 6));
        setTotal((n) => n + fresh.length);
      }
    }
    known.current = ids;
  }, [list.items]);

  const types = useMemo(() => [...new Set(list.items.map((i) => i.incident_type))].sort(), [list.items]);
  const rows = useMemo(
    () =>
      list.items.filter((i) => {
        if (statusFilter === "active" && !ACTIVE_INCIDENT.has(i.status)) return false;
        if (statusFilter !== "active" && statusFilter !== "all" && i.status !== statusFilter) return false;
        return typeFilter === "all" || i.incident_type === typeFilter;
      }),
    [list.items, statusFilter, typeFilter],
  );

  const columns: Column<Incident>[] = [
    { key: "severity", header: "Severity", sortValue: (i) => SEVERITY_RANK[i.severity] ?? 9, render: (i) => <StatusChip domain="severity" value={i.severity} /> },
    {
      key: "incident",
      header: "Incident",
      sortValue: (i) => `${i.incident_type} ${i.network_element_id}`,
      render: (i) => (
        <Link to={`/incidents/${i.incident_id}`}>
          {words(i.incident_type)} at {i.network_element_id}
        </Link>
      ),
    },
    { key: "status", header: "Status", sortValue: (i) => i.status, render: (i) => <StatusChip domain="incident" value={i.status} /> },
    { key: "owner", header: "Owner", sortValue: (i) => i.owner_role ?? "", render: (i) => desk(i.owner_role) },
    { key: "confidence", header: "Confidence", numeric: true, sortValue: (i) => i.confidence, render: (i) => <>{number(i.confidence * 100, 0, "%")} <TruthBadge label="inferred" /></> },
    { key: "opened", header: "Opened", sortValue: (i) => i.opened_at, render: (i) => utcClock(i.opened_at) },
    { key: "updated", header: "Updated", sortValue: (i) => i.updated_at, render: (i) => utcClock(i.updated_at) },
  ];

  if (list.loading) {
    return (
      <Page title="Incidents">
        <LoadingState what="incidents" />
      </Page>
    );
  }
  if (list.error && list.items.length === 0) {
    return (
      <Page title="Incidents">
        <ErrorState what="The incident queue" error={list.error} onRetry={list.reload} />
      </Page>
    );
  }

  return (
    <Page title="Incidents" subtitle="Critical first. An incident is what the detectors' evidence adds up to; its cause is a hypothesis until a person verifies it.">
      {list.error ? <Banner tone="warn">The queue could not be refreshed. What is shown was current at the last successful refresh.</Banner> : null}
      <div className="filters">
        <Select
          label="Status"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "active", label: "Active (not resolved)" },
            { value: "all", label: "All" },
            { value: "open", label: "Open" },
            { value: "acknowledged", label: "Acknowledged" },
            { value: "investigating", label: "Investigating" },
            { value: "escalated", label: "Escalated" },
            { value: "resolved", label: "Resolved" },
            { value: "reopened", label: "Reopened" },
          ]}
        />
        <Select label="Type" value={typeFilter} onChange={setTypeFilter} options={[{ value: "all", label: "All types" }, ...types.map((t) => ({ value: t, label: words(t) }))]} />
      </div>
      <p role="status" className="muted">
        {rows.length} {rows.length === 1 ? "incident" : "incidents"} shown.
      </p>
      <DataTable
        caption="Incident queue"
        columns={columns}
        rows={rows}
        rowKey={(i) => i.incident_id}
        defaultSort={{ key: "severity", direction: "asc" }}
        empty={<EmptyState title="No incident matches">{statusFilter === "active" ? "Nothing is active. New incidents appear here as the detectors raise them." : "Change the status or type filter."}</EmptyState>}
      />
      <LoadMore shown={list.items.length} hasMore={list.hasMore} loading={list.loadingMore} onMore={list.loadMore} noun="incidents loaded" />
      <LiveRegion label="New incidents" entries={announce} total={total} paused={paused} onTogglePause={togglePause} />
    </Page>
  );
}
