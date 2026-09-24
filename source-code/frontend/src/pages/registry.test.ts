import { describe, expect, it } from "vitest";
import { SCREENS } from "../config/access";
import { PAGES } from "./registry";

const SPECIAL = new Set(["login", "forbidden", "session-expired", "not-found"]);

describe("every screen in the access inventory is built", () => {
  it("has a page for each one, and no page for a screen the inventory does not list", () => {
    const ids = SCREENS.filter((s) => !SPECIAL.has(s.id)).map((s) => s.id);
    expect(ids.filter((id) => !PAGES[id])).toEqual([]);
    expect(Object.keys(PAGES).filter((id) => !ids.includes(id))).toEqual([]);
  });
});
