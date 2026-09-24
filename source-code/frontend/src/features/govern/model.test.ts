import { describe, expect, it } from "vitest";
import statusJson from "../../design/status.json";
import { ageWords, auditActionText, detailText, entityPath, itemPath, overallSentence, type OpsStatus } from "./model";

const ingestion = { window_minutes: 5, events: 10, lag_p50_s: 1, lag_p95_s: 2, newest_received_at: null, since_newest_s: null, rejected: {}, status: "healthy", detail: "ok" };
const base = (counts: Partial<OpsStatus["counts"]>, over: Partial<Pick<OpsStatus, "sources" | "analytics" | "ingestion">> = {}) => ({
  counts: { healthy: 0, degraded: 0, down: 0, unknown: 0, ...counts },
  sources: [],
  analytics: [],
  ingestion,
  ...over,
});

describe("audit wording", () => {
  it("puts known actions in words, refused requests as what was tried, and history rows as they came", () => {
    expect(auditActionText({ action: "command.approve", source: "operator" })).toBe("Approved a command");
    expect(auditActionText({ action: "GET /api/v1/audit", source: "operator" })).toBe("Tried GET /api/v1/audit");
    expect(auditActionText({ action: "command moved from requested to approved", source: "command_history" })).toBe("Command moved from requested to approved");
    expect(auditActionText({ action: "some.new_action", source: "operator" })).toBe("Some new action");
  });

  it("links only the entities that have a screen", () => {
    expect(entityPath("incident", "abc")).toBe("/incidents/abc");
    expect(entityPath("command", "abc")).toBe("/actions/commands/abc");
    expect(entityPath("emergency_call", "abc")).toBe("/dispatch/abc");
    expect(entityPath("emergency_assignment", "abc")).toBeNull();
    expect(entityPath("endpoint", null)).toBeNull();
    expect(itemPath({ kind: "call", ref: "c1" })).toBe("/dispatch/c1");
    expect(itemPath({ kind: "other", ref: null })).toBeNull();
  });

  it("summarises detail without dumping structures or showing empty values", () => {
    expect(detailText({ reason: "four-eyes", n: 3, items: [1, 2], nested: { a: 1 }, empty: "", none: null })).toBe("reason: four-eyes; n: 3; items: 2 items; nested: recorded");
    expect(detailText({})).toBe("");
  });
});

describe("platform status wording", () => {
  it("never folds unknown into healthy, and says how many checks have no answer", () => {
    const only = overallSentence(base({ unknown: 3 }));
    expect(only.tone).toBe("warn");
    expect(only.text).toContain("Nothing could be confirmed healthy");
    const mixed = overallSentence(base({ healthy: 5, unknown: 3 }));
    expect(mixed.tone).toBe("info");
    expect(mixed.text).toContain("3 checks have no answer and are not counted as healthy");
    expect(overallSentence(base({ healthy: 5, unknown: 1 })).text).toContain("1 check has no answer and is not counted as healthy");
  });

  it("leads with what needs attention: down is danger, degraded, stale sources and late ingestion are warnings", () => {
    expect(overallSentence(base({ healthy: 4, down: 1 })).tone).toBe("danger");
    expect(overallSentence(base({ healthy: 4, down: 1 })).text).toContain("1 down");
    expect(overallSentence(base({ healthy: 4, degraded: 2 })).text).toContain("2 degraded");
    const stale = [{ id: "a", label: "a", newest_observation_time: null, age_s: null, budget_s: 1, status: "stale" as const }];
    const withStale = overallSentence(base({ healthy: 4 }, { sources: stale }));
    expect(withStale.tone).toBe("warn");
    expect(withStale.text).toContain("1 data source stale");
    expect(overallSentence(base({ healthy: 4 }, { ingestion: { ...ingestion, status: "degraded" } })).text).toContain("ingestion is late");
    expect(overallSentence(base({ healthy: 4 })).text).toBe("4 healthy, nothing degraded, down or stale.");
  });

  it("writes ages without inventing one", () => {
    expect(ageWords(null)).toBe("not measured");
    expect(ageWords(45)).toBe("45 s");
    expect(ageWords(600)).toBe("10.0 min");
    expect(ageWords(7200)).toBe("2.0 h");
  });
});

describe("status vocabulary for the governance screens", () => {
  const domains = statusJson.domains as unknown as Record<string, Record<string, { shape: string; label: string }>>;
  it.each(["service", "audit", "handover", "run"])("%s has a shape and a word per state, distinct within the domain", (domain) => {
    const states = Object.values(domains[domain]!);
    expect(states.length).toBeGreaterThan(1);
    expect(new Set(states.map((s) => s.shape)).size).toBe(states.length);
    expect(new Set(states.map((s) => s.label)).size).toBe(states.length);
  });
});
