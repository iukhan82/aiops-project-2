import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { EmergencyCall, Topology } from "../api/types";
import { useAction } from "../api/useAction";
import { useApi } from "../api/useApi";
import { useCursorList } from "../api/useCursorList";
import { useAuth } from "../auth/AuthContext";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { DataTable, type Column } from "../components/DataTable";
import { Dialog } from "../components/Dialog";
import { usePolledFeed } from "../components/Feed";
import { LiveRegion } from "../components/LiveRegion";
import { Page } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip, TruthBadge } from "../components/badges";
import { Select, TextField } from "../components/controls";
import { LoadMore } from "../components/lifecycle";
import { utcClock, words } from "../lib/format";

const PRIORITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };
const CLOSED = new Set(["cleared", "cancelled"]);

function NewCallDialog({ open, onClose, intersections }: { open: boolean; onClose: () => void; intersections: string[] }) {
  const navigate = useNavigate();
  const save = useAction();
  const [callType, setCallType] = useState("ambulance");
  const [subtype, setSubtype] = useState("");
  const [priority, setPriority] = useState("high");
  const [reliability, setReliability] = useState("verified_dispatch");
  const [where, setWhere] = useState("");
  const [errors, setErrors] = useState<{ subtype?: string; where?: string }>({});
  const summary = useRef<HTMLDivElement>(null);
  const location = where || intersections[0] || "";

  const submit = async () => {
    const found: typeof errors = {};
    if (!subtype.trim()) found.subtype = "Say what the call is about, for example medical emergency or traffic collision.";
    else if (!/^[A-Za-z][A-Za-z0-9_ ]*$/.test(subtype.trim())) found.subtype = "Use letters, numbers, spaces and underscores only.";
    if (!location) found.where = "Choose where the incident is.";
    setErrors(found);
    if (Object.keys(found).length > 0) {
      summary.current?.focus();
      return;
    }
    const outcome = await save.run(() => api<{ call_id: string }>("/api/v1/emergency/calls", { method: "POST", body: { call_type: callType, call_subtype: subtype.trim(), priority, source_reliability: reliability, intersection_id: location } }));
    if (outcome.ok) {
      onClose();
      navigate(`/dispatch/${outcome.value.call_id}`);
    }
  };

  return (
    <Dialog open={open} title="Take a new call" onClose={onClose}>
      <div ref={summary} tabIndex={-1} role={Object.keys(errors).length ? "alert" : undefined}>
        {Object.keys(errors).length > 0 ? <Banner tone="danger">Fix the highlighted fields to take the call.</Banner> : null}
      </div>
      <Select label="Call type" value={callType} onChange={setCallType} options={[{ value: "ambulance", label: "Ambulance" }, { value: "fire", label: "Fire" }, { value: "police", label: "Police" }]} />
      <TextField label="What is it about" value={subtype} onChange={setSubtype} required error={errors.subtype} hint="A short operational subtype. No medical or personal details." />
      <Select label="Priority" value={priority} onChange={setPriority} options={[{ value: "critical", label: "Critical" }, { value: "high", label: "High" }, { value: "medium", label: "Medium" }, { value: "low", label: "Low" }]} />
      <Select label="How reliable is the source" value={reliability} onChange={setReliability} options={[{ value: "verified_dispatch", label: "Verified dispatch" }, { value: "unverified_report", label: "Unverified report" }, { value: "automated_detection", label: "Automated detection" }]} />
      <Select label="Where" value={location} onChange={setWhere} options={intersections.map((i) => ({ value: i, label: i }))} hint={errors.where} />
      {save.error ? <Banner tone="danger" alert>{save.error.message}</Banner> : null}
      <div className="dialog-actions">
        <Button data-autofocus variant="secondary" onClick={onClose} disabled={save.busy}>
          Cancel
        </Button>
        <Button variant="primary" busy={save.busy} onClick={() => void submit()}>
          Take call
        </Button>
      </div>
      <p className="muted">The call is recorded as operator entered.</p>
    </Dialog>
  );
}

export function DispatchPage() {
  const { can } = useAuth();
  const canDispatch = can("emergency.dispatch");
  const [paused, setPaused] = useState(false);
  const [statusFilter, setStatusFilter] = useState("active");
  const [typeFilter, setTypeFilter] = useState("all");
  const [creating, setCreating] = useState(false);
  const list = useCursorList<EmergencyCall>("/api/v1/emergency/calls", {}, paused ? undefined : 10_000);
  const topology = useApi<Topology>(canDispatch ? "/api/v1/network/topology" : null);
  const [announce, setAnnounce] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const known = useRef<Set<string> | null>(null);

  const togglePause = useCallback(() => setPaused((p) => !p), []);
  usePolledFeed([list], list.items[0]?.reported_at ?? null, paused, togglePause);

  useEffect(() => {
    if (list.items.length === 0 && known.current === null) return;
    const ids = new Set(list.items.map((c) => c.call_id));
    if (known.current) {
      const fresh = list.items.filter((c) => !known.current!.has(c.call_id));
      if (fresh.length > 0) {
        setAnnounce((current) => [...fresh.map((c) => `${utcClock(c.reported_at)} new ${c.priority} ${words(c.call_type)} call: ${words(c.call_subtype)}`), ...current].slice(0, 6));
        setTotal((n) => n + fresh.length);
      }
    }
    known.current = ids;
  }, [list.items]);

  const rows = useMemo(
    () =>
      list.items.filter((c) => {
        if (statusFilter === "active" && CLOSED.has(c.status)) return false;
        if (statusFilter !== "active" && statusFilter !== "all" && c.status !== statusFilter) return false;
        return typeFilter === "all" || c.call_type === typeFilter;
      }),
    [list.items, statusFilter, typeFilter],
  );

  const columns: Column<EmergencyCall>[] = [
    { key: "priority", header: "Priority", sortValue: (c) => PRIORITY_RANK[c.priority] ?? 9, render: (c) => <StatusChip domain="severity" value={c.priority} /> },
    {
      key: "call",
      header: "Call",
      sortValue: (c) => `${c.call_type} ${c.call_subtype}`,
      render: (c) => (
        <Link to={`/dispatch/${c.call_id}`}>
          {words(c.call_type)}: {words(c.call_subtype)}
        </Link>
      ),
    },
    { key: "status", header: "Status", sortValue: (c) => c.status, render: (c) => <StatusChip domain="call" value={c.status} /> },
    { key: "reported", header: "Reported", sortValue: (c) => c.reported_at, render: (c) => utcClock(c.reported_at) },
    { key: "source", header: "Source", sortValue: (c) => c.source_reliability, render: (c) => words(c.source_reliability) },
    { key: "truth", header: "Truth label", render: (c) => <TruthBadge label={c.truth_label} /> },
  ];

  if (list.loading) {
    return (
      <Page title="Emergency dispatch">
        <LoadingState what="calls" />
      </Page>
    );
  }
  if (list.error && list.items.length === 0) {
    return (
      <Page title="Emergency dispatch">
        <ErrorState what="The call list" error={list.error} onRetry={list.reload} />
      </Page>
    );
  }

  return (
    <Page
      title="Emergency dispatch"
      subtitle="Calls by type and status. Units are assigned and routed from a call's own screen."
      actions={
        canDispatch ? (
          <Button variant="primary" onClick={() => setCreating(true)}>
            Take a new call
          </Button>
        ) : null
      }
    >
      {list.error ? <Banner tone="warn">The call list could not be refreshed. What is shown was current at the last refresh.</Banner> : null}
      <div className="filters">
        <Select
          label="Status"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "active", label: "Active (not cleared)" },
            { value: "all", label: "All" },
            { value: "received", label: "Received" },
            { value: "dispatched", label: "Dispatched" },
            { value: "unit_assigned", label: "Unit assigned" },
            { value: "en_route", label: "En route" },
            { value: "on_scene", label: "On scene" },
            { value: "cleared", label: "Cleared" },
            { value: "cancelled", label: "Cancelled" },
          ]}
        />
        <Select label="Type" value={typeFilter} onChange={setTypeFilter} options={[{ value: "all", label: "All types" }, { value: "ambulance", label: "Ambulance" }, { value: "fire", label: "Fire" }, { value: "police", label: "Police" }]} />
      </div>
      <p role="status" className="muted">
        {rows.length} {rows.length === 1 ? "call" : "calls"} shown.
      </p>
      <DataTable
        caption="Emergency calls"
        columns={columns}
        rows={rows}
        rowKey={(c) => c.call_id}
        defaultSort={{ key: "priority", direction: "asc" }}
        empty={<EmptyState title="No call matches">{statusFilter === "active" ? "No call is active." : "Change the status or type filter."}{canDispatch ? " Use Take a new call to record one." : ""}</EmptyState>}
      />
      <LoadMore shown={list.items.length} hasMore={list.hasMore} loading={list.loadingMore} onMore={list.loadMore} noun="calls loaded" />
      <LiveRegion label="New calls" entries={announce} total={total} paused={paused} onTogglePause={togglePause} />
      {canDispatch ? <NewCallDialog open={creating} onClose={() => setCreating(false)} intersections={(topology.data?.intersections ?? []).map((i) => i.intersection_id).sort()} /> : null}
    </Page>
  );
}
