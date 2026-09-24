import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { CommandRecord } from "../api/types";
import { useCursorList } from "../api/useCursorList";
import { useAuth } from "../auth/AuthContext";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { useNow } from "../components/Clock";
import { DataTable, type Column } from "../components/DataTable";
import { usePolledFeed } from "../components/Feed";
import { Page } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip } from "../components/badges";
import { Select } from "../components/controls";
import { LoadMore } from "../components/lifecycle";
import { ACTION_WORDS, commandOrder, commandState, expiresIn } from "../features/actions/model";
import { NewCommandDialog } from "../features/actions/NewCommandDialog";
import { ReviewDialog } from "../features/actions/ReviewDialog";
import { utcClock, words } from "../lib/format";

export function CommandsPage() {
  const { can, state } = useAuth();
  const now = useNow();
  const me = state.status === "authenticated" ? state.me.username : "";
  const canReview = can("commands.review");
  const canRequest = can("commands.request");
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState("all");
  const list = useCursorList<CommandRecord>("/api/v1/commands", {}, paused ? undefined : 5_000);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const togglePause = useCallback(() => setPaused((p) => !p), []);
  usePolledFeed([list], list.items[0]?.requested_at ?? null, paused, togglePause);

  const rows = useMemo(() => [...list.items].filter((c) => filter === "all" || commandState(c) === filter).sort(commandOrder), [list.items, filter]);
  const waiting = list.items.filter((c) => c.status === "requested").length;

  const columns: Column<CommandRecord>[] = [
    { key: "state", header: "State", sortValue: (c) => commandState(c), render: (c) => <StatusChip domain="command" value={commandState(c)} /> },
    {
      key: "command",
      header: "Command",
      sortValue: (c) => `${c.action_type} ${c.target.entity_id}`,
      render: (c) => (
        <Link to={`/actions/commands/${c.command_id}`}>
          {ACTION_WORDS[c.action_type] ?? words(c.action_type)} on {c.target.entity_id}
        </Link>
      ),
    },
    { key: "by", header: "Requested by", sortValue: (c) => c.requested_by, render: (c) => c.requested_by },
    { key: "when", header: "Requested", sortValue: (c) => c.requested_at, render: (c) => utcClock(c.requested_at) },
    { key: "approved", header: "Approved by", sortValue: (c) => c.approved_by ?? "", render: (c) => c.approved_by ?? <span className="muted">not approved</span> },
    { key: "expires", header: "Expires", render: (c) => (c.status === "requested" || c.status === "approved" ? expiresIn(c.expires_at, now) : <span className="muted">not applicable</span>) },
    {
      key: "act",
      header: "Decision",
      render: (c) =>
        c.status === "requested" && canReview ? (
          c.requested_by === me ? (
            <span className="muted">You requested it; someone else decides</span>
          ) : (
            <Button variant="secondary" onClick={() => setReviewing(c.command_id)}>
              Review
            </Button>
          )
        ) : (
          <span className="muted">none needed</span>
        ),
    },
  ];

  if (list.loading) {
    return (
      <Page title="Commands and approvals">
        <LoadingState what="commands" />
      </Page>
    );
  }
  if (list.error && list.items.length === 0) {
    return (
      <Page title="Commands and approvals">
        <ErrorState what="The command list" error={list.error} onRetry={list.reload} />
      </Page>
    );
  }

  return (
    <Page
      title="Commands and approvals"
      subtitle="Waiting commands first. Every lifecycle state has its own shape and word; a person requests and a different person approves, and neither executes."
      actions={
        canRequest ? (
          <Button variant="secondary" onClick={() => setCreating(true)}>
            New command
          </Button>
        ) : null
      }
    >
      {list.error ? <Banner tone="warn">The list could not be refreshed. What is shown was current at the last refresh.</Banner> : null}
      {waiting > 0 ? (
        <Banner tone="info">
          {waiting} {waiting === 1 ? "command is" : "commands are"} waiting for a decision.
        </Banner>
      ) : null}
      <div className="filters">
        <Select
          label="State"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "All states" },
            { value: "requested", label: "Pending approval" },
            { value: "requested_policy_unavailable", label: "Policy unavailable" },
            { value: "approved", label: "Approved" },
            { value: "denied", label: "Denied" },
            { value: "expired", label: "Expired" },
            { value: "executing", label: "Executing" },
            { value: "executed", label: "Executed" },
            { value: "failed", label: "Failed" },
            { value: "rolled_back", label: "Rolled back" },
          ]}
        />
      </div>
      <p role="status" className="muted">
        {rows.length} {rows.length === 1 ? "command" : "commands"} shown.
      </p>
      <DataTable caption="Commands" columns={columns} rows={rows} rowKey={(c) => c.command_id} empty={<EmptyState title="No command in this state">Change the state filter, or request a command from a recommendation.</EmptyState>} />
      <LoadMore shown={list.items.length} hasMore={list.hasMore} loading={list.loadingMore} onMore={list.loadMore} noun="commands loaded" />
      <ReviewDialog commandId={reviewing} open={reviewing !== null} now={now} onClose={() => setReviewing(null)} onDone={list.reload} />
      {canRequest ? <NewCommandDialog open={creating} onClose={() => setCreating(false)} onDone={list.reload} /> : null}
    </Page>
  );
}
