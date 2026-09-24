import { describe, expect, it } from "vitest";
import type { CommandRecord } from "../../api/types";
import statusJson from "../../design/status.json";
import { commandLabel, commandOrder, commandState, commandSteps, isNoAction } from "./model";

const command = (status: string, policy = "pending", requestedAt = "2026-09-21T06:00:00Z"): CommandRecord => ({
  command_id: status + requestedAt,
  idempotency_key: "k",
  action_type: "diversion",
  target: { adapter: "a", entity_id: "e" },
  requested_by: "alex",
  requested_at: requestedAt,
  expires_at: "2026-09-21T07:00:00Z",
  policy_decision: policy,
  status,
});

describe("command states", () => {
  it("shows a waiting command whose policy check could not run as its own state, never as approved", () => {
    expect(commandState(command("requested", "policy_unavailable"))).toBe("requested_policy_unavailable");
    expect(commandState(command("requested", "pending"))).toBe("requested");
    expect(commandLabel("requested_policy_unavailable")).toBe("Policy unavailable");
  });

  it("has a distinct label, shape and tone for every command state in the vocabulary", () => {
    const seen = new Set<string>();
    for (const state of Object.keys(statusJson.domains.command)) {
      const entry = (statusJson.domains.command as Record<string, { label: string; shape: string; tone: string }>)[state]!;
      const key = `${entry.shape}/${entry.tone}`;
      expect(seen.has(key), state).toBe(false);
      seen.add(key);
    }
  });

  it("draws the path a command took and ends on where it ended", () => {
    expect(commandSteps(command("executed")).map((s) => s.state)).toEqual(["done", "done", "done", "current"]);
    expect(commandSteps(command("denied")).map((s) => s.id)).toEqual(["requested", "denied"]);
    expect(commandSteps(command("failed")).map((s) => s.id)).toEqual(["requested", "approved", "executing", "failed"]);
    expect(commandSteps(command("rolled_back")).map((s) => s.id)).toEqual(["requested", "approved", "executing", "executed", "rolled_back"]);
    expect(commandSteps(command("expired")).at(-1)?.state).toBe("current");
  });

  it("puts commands waiting for a person first, then newest first", () => {
    const rows = [command("executed", "approved", "2026-09-21T06:30:00Z"), command("requested", "pending", "2026-09-21T06:10:00Z"), command("denied", "denied", "2026-09-21T06:40:00Z")];
    expect(rows.sort(commandOrder).map((c) => c.status)).toEqual(["requested", "denied", "executed"]);
  });

  it("does not offer 'take no action' as something to request", () => {
    expect(isNoAction({ description: "Take no action" })).toBe(true);
    expect(isNoAction({ description: "Extend int-a1 green by 10 s" })).toBe(false);
  });
});
