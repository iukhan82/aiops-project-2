import inventoryJson from "./inventory.json";

export interface ScreenDef {
  id: string;
  path: string;
  title: string;
  capability: string | null;
  task: string;
  live: boolean;
  api: string[];
  optional_api: string[];
  notes: string;
}

interface Inventory {
  roles: Record<string, { home: string; summary: string }>;
  capabilities: Record<string, { roles: string[] }>;
  screens: ScreenDef[];
}

const inventory = inventoryJson as unknown as Inventory;

export const SCREENS: readonly ScreenDef[] = inventory.screens;
export const ROLES = Object.keys(inventory.roles);

export const NAV_GROUPS: readonly { label: string; ids: readonly string[] }[] = [
  { label: "Operate", ids: ["map", "incidents", "dispatch", "field"] },
  { label: "Analyse", ids: ["analytics-corridors", "analytics-intersections", "analytics-devices"] },
  { label: "Act", ids: ["actions-recommendations", "actions-commands", "actions-outcomes"] },
  { label: "Govern", ids: ["handover", "operations", "audit"] },
  { label: "Demonstrate", ids: ["demo"] },
];

export function capabilitiesOf(roles: readonly string[]): Set<string> {
  const held = new Set(roles);
  return new Set(
    Object.entries(inventory.capabilities)
      .filter(([, spec]) => spec.roles.some((r) => held.has(r)))
      .map(([cap]) => cap),
  );
}

export function screenById(id: string): ScreenDef | undefined {
  return SCREENS.find((s) => s.id === id);
}

export function screenAllowed(screen: ScreenDef, capabilities: ReadonlySet<string>): boolean {
  return screen.capability === null || capabilities.has(screen.capability);
}

export function navigationFor(capabilities: ReadonlySet<string>): { label: string; screens: ScreenDef[] }[] {
  return NAV_GROUPS.map((group) => ({
    label: group.label,
    screens: group.ids.map(screenById).filter((s): s is ScreenDef => s !== undefined && screenAllowed(s, capabilities)),
  })).filter((group) => group.screens.length > 0);
}

export function homePathFor(homeScreenId: string | null | undefined): string {
  const screen = homeScreenId ? screenById(homeScreenId) : undefined;
  return screen?.path ?? "/login";
}

export function hasCapability(capabilities: ReadonlySet<string>, capability: string): boolean {
  return capabilities.has(capability);
}
