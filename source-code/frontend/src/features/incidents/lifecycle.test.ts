import { describe, expect, it } from "vitest";
import statusJson from "../../design/status.json";
import { ALLOWED, FLOW, LABEL, stepsFor } from "./lifecycle";

describe("incident lifecycle", () => {
  it("covers exactly the incident statuses the status vocabulary names", () => {
    expect(Object.keys(ALLOWED).sort()).toEqual(Object.keys(statusJson.domains.incident).sort());
    for (const status of Object.keys(ALLOWED)) expect(LABEL[status], status).toBeTruthy();
  });

  it("only ever offers a step the state machine allows", () => {
    for (const [status, next] of Object.entries(ALLOWED)) {
      const offered = stepsFor(status, true, () => undefined, false).filter((s) => s.onSelect).map((s) => s.id);
      for (const id of offered) expect(next, `${status} -> ${id}`).toContain(id);
    }
  });

  it("offers no control at all to someone who may not manage incidents", () => {
    expect(stepsFor("open", false, () => undefined, false).some((s) => s.onSelect)).toBe(false);
  });

  it("marks done, current and upcoming steps by position", () => {
    const steps = stepsFor("investigating", true, () => undefined, false);
    expect(steps.map((s) => s.state)).toEqual(["done", "done", "current", "upcoming", "upcoming"]);
    expect(steps.map((s) => s.id)).toEqual([...FLOW]);
  });

  it("shows a reopened incident between open and acknowledged", () => {
    expect(stepsFor("reopened", true, () => undefined, false).map((s) => s.id)).toEqual(["open", "reopened", "acknowledged", "investigating", "escalated", "resolved"]);
  });
});
