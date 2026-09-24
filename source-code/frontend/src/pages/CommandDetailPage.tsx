import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import type { CommandDetail } from "../api/types";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { useNow } from "../components/Clock";
import { usePolledFeed } from "../components/Feed";
import { KeyValueList } from "../components/KeyValueList";
import { Page, Panel } from "../components/Page";
import { ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip, TruthBadge } from "../components/badges";
import { Stepper, Timeline, type TimelineEntry } from "../components/lifecycle";
import { ACTION_WORDS, SAFETY_WORDS, commandLabel, commandState, commandSteps, expiresIn } from "../features/actions/model";
import { ReviewDialog } from "../features/actions/ReviewDialog";
import { number, utcDateTime, words } from "../lib/format";

export function CommandDetailPage() {
  const { commandId = "" } = useParams();
  const { can, state } = useAuth();
  const now = useNow();
  const me = state.status === "authenticated" ? state.me.username : "";
  const [paused, setPaused] = useState(false);
  const detail = useApi<CommandDetail>(`/api/v1/commands/${encodeURIComponent(commandId)}`, undefined, paused ? undefined : 3_000);
  const [reviewing, setReviewing] = useState(false);
  const togglePause = useCallback(() => setPaused((p) => !p), []);
  usePolledFeed([detail], detail.data?.transitions.at(-1)?.changed_at ?? null, paused, togglePause);

  const timeline = useMemo<TimelineEntry[]>(
    () => (detail.data?.transitions ?? []).map((t, i) => ({ id: String(i), at: t.changed_at, source: t.changed_by, text: `${t.from_status ? commandLabel(t.from_status) : "Created"} to ${commandLabel(t.to_status)}${t.note ? `: ${t.note}` : ""}` })),
    [detail.data],
  );

  if (detail.loading) {
    return (
      <Page title="Command detail">
        <LoadingState what="the command" />
      </Page>
    );
  }
  if (!detail.data) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <Page title="Command detail">
        {notFound ? (
          <div role="status" className="state">
            <h2>There is no such command</h2>
            <p>
              <Link to="/actions/commands">Back to commands</Link>
            </p>
          </div>
        ) : (
          <ErrorState what="The command" error={detail.error} onRetry={detail.reload} />
        )}
      </Page>
    );
  }

  const { command, outcome, params, recommendation } = detail.data;
  const stateName = commandState(command);
  const waiting = command.status === "requested";
  const settings = params ? Object.entries(params).filter(([k]) => k !== "basis") : [];

  return (
    <Page
      title="Command detail"
      subtitle={
        <>
          {ACTION_WORDS[command.action_type] ?? words(command.action_type)} on {command.target.entity_id}. <Link to="/actions/commands">All commands</Link>
        </>
      }
      actions={
        waiting && can("commands.review") && command.requested_by !== me ? (
          <Button variant="primary" onClick={() => setReviewing(true)}>
            Review
          </Button>
        ) : null
      }
    >
      {stateName === "requested_policy_unavailable" ? (
        <Banner tone="warn">The policy check could not run when this was reviewed, so it is still waiting. It was not approved. {command.error?.message}</Banner>
      ) : null}
      {command.error && stateName !== "requested_policy_unavailable" ? (
        <Banner tone="danger" alert>
          {commandLabel(stateName)}: {command.error.message} ({command.error.error_code}, {command.error.retryable ? "may be retried" : "will not succeed if retried"}).
        </Banner>
      ) : null}
      {waiting && command.requested_by === me ? <Banner tone="info">You requested this command, so someone else has to approve or deny it.</Banner> : null}

      <div className="two-col">
        <div className="stack">
          <Panel title="Lifecycle" id="command-lifecycle">
            <p>
              <StatusChip domain="command" value={stateName} />
            </p>
            <Stepper label="Command lifecycle" steps={commandSteps(command)} />
            <p className="muted">A person requests, a different person approves, and the command executor service executes. No person executes a command.</p>
          </Panel>
          <Panel title="Details" id="command-details">
            <KeyValueList
              items={[
                { label: "Action", value: ACTION_WORDS[command.action_type] ?? words(command.action_type) },
                { label: "Safety class", value: SAFETY_WORDS[detail.data.safety_class] ?? detail.data.safety_class },
                { label: "Target", value: `${command.target.entity_id} (${words(command.target.adapter)})` },
                { label: "Requested by", value: `${command.requested_by}${detail.data.requested_by_role ? ` (${words(detail.data.requested_by_role)})` : ""}` },
                { label: "Requested", value: utcDateTime(command.requested_at) },
                { label: "Approved by", value: command.approved_by ? `${command.approved_by}${detail.data.approved_by_role ? ` (${words(detail.data.approved_by_role)})` : ""}` : "not approved" },
                ...(command.approved_at ? [{ label: "Approved", value: utcDateTime(command.approved_at) }] : []),
                { label: "Policy result", value: words(command.policy_decision) },
                { label: "Expires", value: `${utcDateTime(command.expires_at)}${waiting || command.status === "approved" ? ` (${expiresIn(command.expires_at, now)})` : ""}` },
                ...(command.acknowledged_at ? [{ label: "Adapter acknowledged", value: utcDateTime(command.acknowledged_at) }] : []),
                ...(command.rolled_back_at ? [{ label: "Rolled back", value: utcDateTime(command.rolled_back_at) }] : []),
                ...(settings.length > 0 ? [{ label: "Settings", value: settings.map(([k, v]) => `${words(k)} ${String(v)}`).join("; ") }] : []),
                ...(recommendation ? [{ label: "From recommendation", value: <Link to="/actions/recommendations">{words(recommendation.action_type)}</Link>, extra: <StatusChip domain="recommendation" value={recommendation.status} /> }] : [{ label: "From recommendation", value: "Requested directly" }]),
              ]}
            />
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Verified outcome" id="command-outcome">
            {outcome ? (
              <>
                <p>
                  <StatusChip domain="outcome" value={outcome.classification} />
                </p>
                <KeyValueList
                  items={[
                    { label: "Before", value: outcome.pre_window.measurements.map((m) => `${words(m.name)} ${number(m.value, 1, m.unit)}`).join("; ") || "no measurement" },
                    { label: "After", value: outcome.post_window.measurements.map((m) => `${words(m.name)} ${number(m.value, 1, m.unit)}`).join("; ") || "no measurement" },
                    { label: "Verified by", value: outcome.verifier, extra: <TruthBadge label="verified" /> },
                    { label: "Rolled back", value: outcome.rollback_triggered ? "Yes, and the undo was confirmed" : "No" },
                    ...(outcome.escalation_reason ? [{ label: "Escalated because", value: outcome.escalation_reason }] : []),
                  ]}
                />
                <p>
                  <Link to="/actions/outcomes">All outcomes</Link>
                </p>
              </>
            ) : command.status === "executed" ? (
              <p className="muted">Executed, and waiting for the outcome verifier. The verifier measures the corridor before and after and records what it found; until then this command is not called successful.</p>
            ) : (
              <p className="muted">There is no outcome: this command has not been executed, so there is nothing to verify.</p>
            )}
          </Panel>
          <Panel title="State history" id="command-history">
            <Timeline label="Command state history" entries={timeline} />
          </Panel>
        </div>
      </div>
      <ReviewDialog commandId={commandId} open={reviewing} now={now} onClose={() => setReviewing(false)} onDone={detail.reload} />
    </Page>
  );
}
