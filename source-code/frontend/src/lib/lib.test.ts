import { describe, expect, it, vi } from "vitest";
import statusJson from "../design/status.json";
import { ageSeconds, ageText, duration, number, percent, utcClock, utcDateTime, words } from "./format";
import { STATUS_DOMAINS, statusEntry, type Domain } from "./status";

describe("formatting is fixed, UTC and locale independent", () => {
  it("formats times in UTC", () => {
    expect(utcClock("2026-09-20T09:14:52Z")).toBe("09:14:52 UTC");
    expect(utcDateTime("2026-09-20T23:59:59+05:00")).toBe("2026-09-20 18:59:59 UTC");
    expect(utcClock(null)).toBe("not available");
    expect(utcClock("not a time")).toBe("not available");
  });
  it("computes and words ages", () => {
    const now = Date.parse("2026-09-20T09:15:00Z");
    expect(ageSeconds("2026-09-20T09:14:57Z", now)).toBe(3);
    expect(ageText(3)).toBe("3 s ago");
    expect(ageText(125)).toBe("2 min 05 s ago");
    expect(ageText(null)).toBe("age not available");
    expect(ageSeconds("2026-09-20T09:16:00Z", now)).toBe(0);
  });
  it("shows numbers with units and never turns unknown into zero", () => {
    expect(number(12345.678, 1, "km/h")).toBe("12,345.7 km/h");
    expect(number(null)).toBe("not available");
    expect(number(undefined, 2, "s")).toBe("not available");
    expect(percent(0.256, 1)).toBe("25.6 %");
    expect(duration(75)).toBe("1 min 15 s");
    expect(words("unit_assigned")).toBe("Unit assigned");
  });
});

describe("status vocabulary", () => {
  it("has a label, shape, tone and meaning for every state of every domain", () => {
    for (const domain of STATUS_DOMAINS) {
      for (const [key, entry] of Object.entries((statusJson.domains as Record<string, Record<string, Record<string, string>>>)[domain]!)) {
        expect(entry.label, `${domain}.${key}`).toBeTruthy();
        expect(statusJson.shapes).toContain(entry.shape);
        expect(statusJson.tones).toContain(entry.tone);
        expect(entry.meaning, `${domain}.${key} meaning`).toBeTruthy();
      }
    }
  });
  it("renders an unknown state as received with a neutral ring, and says so in the console", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const entry = statusEntry("command" as Domain, "teleported");
    expect(entry).toMatchObject({ label: "teleported", shape: "ring", tone: "neutral" });
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
  it("keeps the command lifecycle visually distinct: no two states share both shape and tone", () => {
    const seen = new Set<string>();
    for (const entry of Object.values(statusJson.domains.command)) {
      const key = `${entry.shape}/${entry.tone}`;
      expect(seen.has(key), key).toBe(false);
      seen.add(key);
    }
  });
});
