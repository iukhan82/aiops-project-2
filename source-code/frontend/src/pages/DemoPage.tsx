import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAction } from "../api/useAction";
import { useApi } from "../api/useApi";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { DataTable, type Column } from "../components/DataTable";
import { ConfirmDialog, type ConfirmResult } from "../components/Dialog";
import { KeyValueList } from "../components/KeyValueList";
import { Page, Panel } from "../components/Page";
import { ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip } from "../components/badges";
import type { DemoAuditRecord, ScenarioRun } from "../features/govern/model";
import { number, utcDateTime, words } from "../lib/format";

interface RunList {
  items: ScenarioRun[];
  bound: { max_concurrent: number; running: number };
}

type Mode = "start" | "replay" | "reset";

const MODE_WORDS: Record<Mode, { title: string; confirm: string; does: string }> = {
  start: { title: "Start a scenario run", confirm: "Start run", does: "Replays the recorded scenario events through the platform's ordered, duplicate-safe replay and records which events it accepted." },
  replay: { title: "Replay this run", confirm: "Replay run", does: "Runs the same replay again on this run. The accepted set should come out identical; if it does not, that is a finding." },
  reset: { title: "Reset this run", confirm: "Reset run", does: "Marks the run as reset. The recorded run stays in the list and in the trail; nothing else is undone, because nothing else was changed." },
};

const short = (id: string) => id.slice(0, 8);

/** Confirmation for a demo action. It restates what the action does and, as plainly, what it does not. */
function DemoActionDialog({ mode, run, onClose, onDone }: { mode: Mode | null; run: ScenarioRun | null; onClose: () => void; onDone: (run: ScenarioRun | null) => void }) {
  const save = useAction();
  const [result, setResult] = useState<ConfirmResult | null>(null);
  useEffect(() => setResult(null), [mode, run?.run_id]);

  const confirm = async () => {
    if (!mode) return;
    const path = mode === "start" ? "/scenario-control/v1/runs" : `/scenario-control/v1/runs/${run?.run_id}/${mode}`;
    const outcome = await save.run(() => api<ScenarioRun>(path, { method: "POST" }));
    if (outcome.ok) {
      const accepted = outcome.value.result?.accepted_count;
      setResult({ tone: "ok", title: mode === "reset" ? "Run reset" : "Run completed", message: mode === "reset" ? "The run is marked reset." : `${number(accepted ?? 0)} events accepted. Fingerprint ${outcome.value.result?.accepted_sha256?.slice(0, 12) ?? "not available"}.` });
      onDone(outcome.value);
    } else if (outcome.error.code === "run_bound_reached") {
      setResult({ tone: "warn", title: "Bound reached", message: outcome.error.message });
      onDone(null);
    } else {
      setResult({ tone: "danger", title: "Not done", message: outcome.error.message });
    }
  };

  return (
    <ConfirmDialog
      open={mode !== null}
      title={mode ? MODE_WORDS[mode].title : "Demo action"}
      summary={[
        { label: "Action", value: mode ? words(mode) : "" },
        ...(run ? [{ label: "Run", value: run.run_id }] : []),
        { label: "What it does", value: mode ? MODE_WORDS[mode].does : "" },
        { label: "What it does not do", value: "It does not change the live map, any incident, command, call or outcome, and it sends nothing to a device." },
      ]}
      statements={["This is recorded in the demo audit trail under your name, not in the operator audit trail."]}
      confirmLabel={mode ? MODE_WORDS[mode].confirm : "Confirm"}
      busy={save.busy}
      result={result}
      onConfirm={() => void confirm()}
      onClose={onClose}
    />
  );
}

export function DemoPage() {
  const runs = useApi<RunList>("/scenario-control/v1/runs", { limit: 20 });
  const trail = useApi<{ items: DemoAuditRecord[] }>("/scenario-control/v1/audit", { limit: 50 });
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useApi<ScenarioRun>(selected ? `/scenario-control/v1/runs/${selected}` : null);
  const [dialog, setDialog] = useState<{ mode: Mode; run: ScenarioRun | null } | null>(null);

  const refresh = () => {
    runs.reload();
    trail.reload();
    detail.reload();
  };

  const runColumns: Column<ScenarioRun>[] = [
    {
      key: "run",
      header: "Run",
      sortValue: (r) => r.started_at,
      render: (r) => (
        <button type="button" className="link-button" aria-pressed={selected === r.run_id} onClick={() => setSelected(r.run_id)} aria-label={`Show run ${short(r.run_id)}`}>
          {short(r.run_id)}
        </button>
      ),
    },
    { key: "status", header: "State", sortValue: (r) => r.status, render: (r) => <StatusChip domain="run" value={r.status} /> },
    { key: "by", header: "Started by", sortValue: (r) => r.started_by, render: (r) => r.started_by },
    { key: "at", header: "Started", sortValue: (r) => r.started_at, render: (r) => utcDateTime(r.started_at) },
    { key: "accepted", header: "Events accepted", numeric: true, render: (r) => (r.result?.accepted_count === undefined ? <span className="muted">not available</span> : number(r.result.accepted_count)) },
    { key: "sha", header: "Fingerprint", render: (r) => (r.result?.accepted_sha256 ? <code>{r.result.accepted_sha256.slice(0, 12)}</code> : <span className="muted">none</span>) },
    {
      key: "act",
      header: "Actions",
      render: (r) => (
        <span className="row-actions">
          <Button variant="secondary" onClick={() => setDialog({ mode: "replay", run: r })}>
            Replay
          </Button>
          <Button variant="secondary" onClick={() => setDialog({ mode: "reset", run: r })} disabled={r.status === "reset"}>
            Reset
          </Button>
        </span>
      ),
    },
  ];
  const trailColumns: Column<DemoAuditRecord>[] = [
    { key: "at", header: "Time", nowrap: true, sortValue: (r) => r.at, render: (r) => utcDateTime(r.at) },
    { key: "who", header: "Who", nowrap: true, render: (r) => r.actor ?? <span className="muted">not recorded</span> },
    { key: "action", header: "Action", render: (r) => words(r.action) },
    { key: "outcome", header: "Result", render: (r) => words(r.outcome) },
    { key: "run", header: "Run", render: (r) => (r.run_id ? short(r.run_id) : <span className="muted">none</span>) },
    { key: "detail", header: "Detail", render: (r) => r.detail || <span className="muted">none</span> },
  ];

  if (runs.loading) {
    return (
      <Page title="Demo controls">
        <LoadingState what="the demo runs" />
      </Page>
    );
  }
  if (!runs.data) {
    return (
      <Page title="Demo controls">
        <ErrorState what="The demo controls" error={runs.error} onRetry={runs.reload} />
      </Page>
    );
  }

  const { bound } = runs.data;
  const full = bound.running >= bound.max_concurrent;
  const d = detail.data;
  return (
    <Page
      title="Demo controls"
      subtitle="Start, replay and reset scenario runs. A separate service with its own identity, audit trail and API."
      actions={
        <Button variant="primary" onClick={() => setDialog({ mode: "start", run: null })}>
          Start a scenario run
        </Button>
      }
    >
      <Banner tone="warn">
        <strong>Demonstration controls.</strong> These controls belong to the scenario-control service, not to operations. They replay recorded events; they do not change the live map or any incident, command or outcome. You are signed in as the demo operator, an identity that holds no operational authority. Your actions are recorded in the demo trail below, separate from the operator audit trail.
      </Banner>
      {runs.error ? <Banner tone="warn">The runs could not be refreshed. What is shown was current at the last refresh.</Banner> : null}
      <p role="status">
        {bound.running} of {bound.max_concurrent} runs in progress.{" "}
        {full ? "The bound is reached, so the service will refuse a new run until one finishes." : "A new run can start."}
      </p>

      <div className="two-col">
        <Panel title="Runs" id="demo-runs" actions={<Button variant="secondary" onClick={refresh}>Refresh</Button>}>
          <DataTable caption="Scenario runs" columns={runColumns} rows={runs.data.items} rowKey={(r) => r.run_id} defaultSort={{ key: "run", direction: "desc" }} empty={<p className="muted">No run yet. Start one to replay the recorded scenario.</p>} />
        </Panel>
        <Panel title="Selected run" id="demo-run-detail">
          {!selected ? (
            <p className="muted">Select a run to see what the replay accepted and refused.</p>
          ) : detail.loading ? (
            <LoadingState what="the run" />
          ) : d ? (
            <KeyValueList
              items={[
                { label: "Run", value: d.run_id },
                { label: "State", value: <StatusChip domain="run" value={d.status} /> },
                { label: "Scenario", value: d.scenario },
                { label: "Started", value: `${utcDateTime(d.started_at)} by ${d.started_by}` },
                { label: "Finished", value: d.completed_at ? utcDateTime(d.completed_at) : "not finished" },
                { label: "Events read", value: d.result?.input_count === undefined ? "not available" : number(d.result.input_count) },
                { label: "Events accepted", value: d.result?.accepted_count === undefined ? "not available" : number(d.result.accepted_count) },
                { label: "Events refused", value: d.result?.rejected_count === undefined ? "not available" : number(d.result.rejected_count) },
                { label: "Fingerprint of the accepted set", value: d.result?.accepted_sha256 ?? "none" },
              ]}
            />
          ) : (
            <ErrorState what="The run" error={detail.error} onRetry={detail.reload} />
          )}
        </Panel>
      </div>

      <Panel title="Demo audit trail" id="demo-trail">
        <p className="muted">Newest first. It holds only what the scenario-control service did, including the requests it refused. It cannot be edited.</p>
        {trail.data ? (
          <DataTable caption="Demo audit trail" columns={trailColumns} rows={trail.data.items} rowKey={(r) => String(r.audit_id)} defaultSort={{ key: "at", direction: "desc" }} empty={<p className="muted">Nothing recorded yet.</p>} />
        ) : trail.loading ? (
          <LoadingState what="the trail" />
        ) : (
          <ErrorState what="The demo trail" error={trail.error} onRetry={trail.reload} />
        )}
      </Panel>

      <DemoActionDialog
        mode={dialog?.mode ?? null}
        run={dialog?.run ?? null}
        onClose={() => setDialog(null)}
        onDone={(run) => {
          if (run?.run_id) setSelected(run.run_id);
          refresh();
        }}
      />
    </Page>
  );
}
