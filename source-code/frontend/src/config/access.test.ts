import { describe, expect, it } from "vitest";
import inventory from "./inventory.json";
import { NAV_GROUPS, ROLES, SCREENS, capabilitiesOf, homePathFor, navigationFor, screenAllowed, screenById } from "./access";

const SPECIAL = new Set(["login", "forbidden", "session-expired", "not-found"]);
const navigable = SCREENS.filter((s) => !SPECIAL.has(s.id) && !s.path.includes(":"));

describe("navigation is derived from the inventory", () => {
  it("puts every navigable screen in exactly one group, and only real screens in groups", () => {
    const listed = NAV_GROUPS.flatMap((g) => g.ids);
    expect(new Set(listed).size).toBe(listed.length);
    expect([...listed].sort()).toEqual(navigable.map((s) => s.id).sort());
  });

  it("gives every role a home screen it is allowed to open", () => {
    for (const role of ROLES) {
      const home = (inventory.roles as Record<string, { home: string }>)[role]?.home ?? "";
      const screen = screenById(home);
      expect(screen, `${role} home ${home}`).toBeDefined();
      expect(screenAllowed(screen!, capabilitiesOf([role])), `${role} may open ${home}`).toBe(true);
      expect(homePathFor(home)).toBe(screen!.path);
    }
  });

  it("shows each role only the screens its capabilities allow", () => {
    const seen = (role: string) => navigationFor(capabilitiesOf([role])).flatMap((g) => g.screens.map((s) => s.id));
    expect(seen("auditor")).toContain("audit");
    expect(seen("operator")).not.toContain("audit");
    expect(seen("operator")).not.toContain("demo");
    expect(seen("demo_operator")).toContain("demo");
    expect(seen("demo_operator")).not.toContain("actions-commands");
    expect(seen("dispatcher")).not.toContain("operations");
    expect(seen("field_responder")).toEqual(expect.arrayContaining(["field", "map"]));
    expect(seen("field_responder")).not.toContain("actions-commands");
  });

  it("makes every screen reachable by at least one role", () => {
    for (const screen of SCREENS) {
      const reachable = screen.capability === null || ROLES.some((role) => capabilitiesOf([role]).has(screen.capability!));
      expect(reachable, screen.id).toBe(true);
    }
  });

  it("adds capabilities up across roles and grants none for an unknown role", () => {
    expect(capabilitiesOf(["operator", "auditor"]).has("audit.view")).toBe(true);
    expect(capabilitiesOf(["superuser"]).size).toBe(0);
    expect(capabilitiesOf(["system:command-executor"]).size).toBe(0);
  });
});
