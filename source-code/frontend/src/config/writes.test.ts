import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

// UX-03: every way the UI can change something is listed here and classified. A new write that is not listed fails this test, so a
// critical action cannot be added without a decision about its confirmation. "confirmed" means it is reached only through a dialog that
// restates what will happen, defaults focus to Cancel, disables the decision while it is in flight and shows the server's answer in
// place; the confirmation's behaviour itself is tested in the browser specs named in `proof`.
type Kind = "confirmed" | "form" | "direct";
interface Write {
  file: string;
  call: string;
  kind: Kind;
  what: string;
  proof: string;
  why?: string;
}

const SRC = join(__dirname, "..");

const WRITES: Write[] = [
  { file: "features/actions/NewCommandDialog.tsx", call: '"/api/v1/commands"', kind: "confirmed", what: "request a command", proof: "actions.spec" },
  { file: "features/actions/ReviewDialog.tsx", call: "`/api/v1/commands/${command.command_id}/review`", kind: "confirmed", what: "approve or deny a command", proof: "actions.spec" },
  { file: "pages/RecommendationsPage.tsx", call: "`/api/v1/recommendations/${", kind: "confirmed", what: "request a command from a recommendation", proof: "actions.spec" },
  { file: "features/govern/HandoverDialogs.tsx", call: '"/api/v1/handovers"', kind: "confirmed", what: "write a shift handover", proof: "govern.spec" },
  { file: "features/govern/HandoverDialogs.tsx", call: "`/api/v1/handovers/${handover.handover_id}/acknowledge`", kind: "confirmed", what: "acknowledge a handover", proof: "govern.spec" },
  { file: "pages/DemoPage.tsx", call: "path", kind: "confirmed", what: "start, replay or reset a demo run", proof: "govern.spec" },
  { file: "pages/DispatchDetailPage.tsx", call: "`/api/v1/emergency/calls/${call.call_id}/transition`", kind: "confirmed", what: "cancel a call", proof: "dispatch.spec" },
  { file: "pages/DispatchPage.tsx", call: '"/api/v1/emergency/calls"', kind: "form", what: "take a new call", proof: "dispatch.spec", why: "a data-entry form in a dialog that keeps the person on it when a field is missing; it records a call, it commands nothing" },
  { file: "pages/IncidentDetailPage.tsx", call: "`/api/v1/incidents/${incident.incident_id}/transition`", kind: "confirmed", what: "resolve an incident (the other steps are single legal next steps)", proof: "incidents.spec" },
  { file: "pages/DispatchDetailPage.tsx", call: "`/api/v1/emergency/calls/${call.call_id}/assignments`", kind: "direct", what: "assign a unit to a call", proof: "dispatch.spec", why: "the server refuses an unknown or silent unit and a stale position; nothing physical happens until the next steps are recorded" },
  { file: "pages/DispatchDetailPage.tsx", call: "`/api/v1/emergency/assignments/${assignmentId}/transition`", kind: "direct", what: "record the status a unit reports", proof: "dispatch.spec", why: "one legal next step at a time, as the unit reports it; audited; changes no signal, sign or route" },
  { file: "pages/DispatchDetailPage.tsx", call: "`/api/v1/emergency/assignments/${assignmentId}/route`", kind: "direct", what: "choose one of the server's route alternatives", proof: "dispatch.spec", why: "a choice among alternatives the server computed; audited; commands nothing" },
  { file: "pages/IncidentDetailPage.tsx", call: "`/api/v1/incidents/${incident.incident_id}/notes`", kind: "direct", what: "add a note to an incident", proof: "incidents.spec", why: "append-only text; it is never edited and changes no state" },
  { file: "pages/IncidentDetailPage.tsx", call: "`/api/v1/incidents/${incident.incident_id}/owner`", kind: "direct", what: "change which team owns an incident", proof: "incidents.spec", why: "recorded as a note and audited; reversible; commands nothing" },
];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(entry) && !/\.test\.tsx?$/.test(entry) ? [path] : [];
  });
}

function foundWrites(): { file: string; call: string }[] {
  const found: { file: string; call: string }[] = [];
  for (const path of sources(SRC)) {
    const text = readFileSync(path, "utf-8");
    for (const match of text.matchAll(/api(?:<[^>]*(?:<[^>]*>[^>]*)*>)?\(\s*((?:`[^`]*`|"[^"]*"|path))\s*,\s*\{\s*method:\s*"POST"/g)) {
      found.push({ file: relative(SRC, path).replace(/\\/g, "/"), call: match[1]!.replace(/(\/api\/v1\/recommendations\/)\$\{[\s\S]*$/, "$1${").replace(/`$/, "`") });
    }
  }
  return found;
}

describe("every write the UI can make is listed and classified (UX-03)", () => {
  it("lists exactly the write calls the code makes, so a new one cannot slip in unclassified", () => {
    const code = foundWrites().map((w) => `${w.file} ${w.call}`).sort();
    const listed = WRITES.map((w) => `${w.file} ${w.call}`).sort();
    expect(code).toEqual(listed);
  });

  it("puts every critical action behind a confirmation: commands, approvals, requests, cancellations, resolutions, handovers, demo runs", () => {
    const critical = ["request a command", "approve or deny a command", "request a command from a recommendation", "cancel a call", "resolve an incident", "write a shift handover", "acknowledge a handover", "start, replay or reset a demo run"];
    for (const what of critical) {
      const write = WRITES.find((w) => w.what.startsWith(what));
      expect(write, what).toBeDefined();
      expect(write!.kind, what).toBe("confirmed");
    }
  });

  it("gives every unconfirmed write a reason, and a browser spec that exercises it", () => {
    for (const write of WRITES.filter((w) => w.kind !== "confirmed")) {
      expect(write.why, write.what).toBeTruthy();
      expect(write.proof, write.what).toMatch(/\.spec$/);
    }
  });

  it("uses a confirmation component in every file that holds a confirmed write", () => {
    for (const write of WRITES.filter((w) => w.kind === "confirmed")) {
      const text = readFileSync(join(SRC, write.file), "utf-8");
      expect(/ConfirmDialog|ConfirmBody/.test(text), `${write.file}: ${write.what}`).toBe(true);
    }
  });
});
