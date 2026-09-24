import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useCursorList } from "../api/useCursorList";
import { useAuth } from "../auth/AuthContext";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { Page, Panel } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip } from "../components/badges";
import { Select } from "../components/controls";
import { LoadMore } from "../components/lifecycle";
import { AcknowledgeDialog, WriteHandoverDialog } from "../features/govern/HandoverDialogs";
import { itemPath, type Handover } from "../features/govern/model";
import { utcDateTime, words } from "../lib/format";

const KIND_WORDS: Record<string, string> = { incident: "Incident", command: "Command", call: "Emergency call", other: "Note" };

export function HandoverPage() {
  const { can, state } = useAuth();
  const me = state.status === "authenticated" ? state.me.username : "";
  const canWrite = can("handover.write");
  const [filter, setFilter] = useState("all");
  const list = useCursorList<Handover>("/api/v1/handovers", { status: filter === "all" ? undefined : filter }, 20_000, 20);
  const [writing, setWriting] = useState(false);
  const [acknowledging, setAcknowledging] = useState<Handover | null>(null);
  const waitingForMe = useMemo(() => list.items.filter((h) => h.status === "awaiting_acknowledgement" && h.author !== me), [list.items, me]);

  if (list.loading) {
    return (
      <Page title="Shift handover">
        <LoadingState what="the handovers" />
      </Page>
    );
  }
  if (list.error && list.items.length === 0) {
    return (
      <Page title="Shift handover">
        <ErrorState what="The handovers" error={list.error} onRetry={list.reload} />
      </Page>
    );
  }

  return (
    <Page
      title="Shift handover"
      subtitle="What the outgoing shift hands over: a summary and the open items behind it. The incoming person acknowledges by name; nobody acknowledges their own."
      actions={
        canWrite ? (
          <Button variant="primary" onClick={() => setWriting(true)}>
            Write a handover
          </Button>
        ) : null
      }
    >
      {list.error ? <Banner tone="warn">The list could not be refreshed. What is shown was current at the last refresh.</Banner> : null}
      {canWrite && waitingForMe.length > 0 ? (
        <Banner tone="info">
          {waitingForMe.length} {waitingForMe.length === 1 ? "handover is" : "handovers are"} waiting for someone other than the author to acknowledge.
        </Banner>
      ) : null}
      {!canWrite ? <Banner tone="info">Your role can read handovers. Writing and acknowledging them belongs to the operating roles.</Banner> : null}
      <div className="filters">
        <Select
          label="Show"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "All handovers" },
            { value: "awaiting_acknowledgement", label: "Awaiting acknowledgement" },
            { value: "acknowledged", label: "Acknowledged" },
          ]}
        />
      </div>
      <p role="status" className="muted">
        {list.items.length} {list.items.length === 1 ? "handover" : "handovers"} shown, newest first.
      </p>
      {list.items.length === 0 ? (
        <EmptyState title="No handover yet">{filter === "all" ? "The first one appears here when a shift writes it." : "None is in this state."}</EmptyState>
      ) : (
        <ul className="handover-list" aria-label="Handovers">
          {list.items.map((h) => (
            <li key={h.handover_id}>
              <Panel title={`${h.outgoing_shift} shift to ${h.incoming_shift} shift`} id={`handover-${h.handover_id}`} className="handover">
                <p>
                  <StatusChip domain="handover" value={h.status} />
                </p>
                <p className="handover-meta">
                  Written by {h.author} ({h.author_roles.map(words).join(", ")}), {utcDateTime(h.created_at)}.{" "}
                  {h.acknowledged_by ? `Acknowledged by ${h.acknowledged_by}, ${utcDateTime(h.acknowledged_at)}.` : "Not acknowledged yet."}
                </p>
                <p className="handover-summary">{h.summary}</p>
                <h3>Open items</h3>
                {h.open_items.length === 0 ? (
                  <p className="muted">None were handed over.</p>
                ) : (
                  <ul className="plain-list">
                    {h.open_items.map((item, index) => {
                      const path = itemPath(item);
                      return (
                        <li key={`${item.kind}-${item.ref ?? index}`}>
                          {KIND_WORDS[item.kind] ?? words(item.kind)}: {path ? <Link to={path}>{item.label}</Link> : item.label}
                          {item.status ? ` (${words(item.status).toLowerCase()} when handed over)` : ""}
                          {item.note ? `, ${item.note}` : ""}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {h.status === "awaiting_acknowledgement" && canWrite ? (
                  h.author === me ? (
                    <p className="muted">You wrote this handover, so someone else has to acknowledge it.</p>
                  ) : (
                    <Button variant="primary" onClick={() => setAcknowledging(h)}>
                      Acknowledge
                    </Button>
                  )
                ) : null}
              </Panel>
            </li>
          ))}
        </ul>
      )}
      <LoadMore shown={list.items.length} hasMore={list.hasMore} loading={list.loadingMore} onMore={list.loadMore} noun="handovers" />
      {canWrite ? <WriteHandoverDialog open={writing} onClose={() => setWriting(false)} onDone={list.reload} /> : null}
      <AcknowledgeDialog handover={acknowledging} onClose={() => setAcknowledging(null)} onDone={list.reload} />
    </Page>
  );
}
