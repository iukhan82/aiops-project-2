import { expect, test, type Page } from "@playwright/test";
import { ROLES, expectNoAxeViolations, recordMetrics, screensFor, settled, signIn, tabThrough, userFor, type ScreenSpec } from "./support";

// P08.10 acceptance: UX-01 (WCAG 2.2 AA: keyboard, contrast, reduced motion, per role) and UX-04 (responsive, reflow at 200% and 400%,
// no clipped or unreachable control). Real Chrome, real Keycloak, the real API on the demo database.

const DETAIL_LISTS: Record<string, { list: string; prefix: string }> = {
  "incident-detail": { list: "/incidents", prefix: "/incidents/" },
  "dispatch-detail": { list: "/dispatch", prefix: "/dispatch/" },
  "actions-command-detail": { list: "/actions/commands", prefix: "/actions/commands/" },
};

/** The concrete screens a role can open: parameterised ones (a detail screen) are opened from the first row of their list. */
async function concrete(page: Page, screens: ScreenSpec[]): Promise<ScreenSpec[]> {
  const out: ScreenSpec[] = [];
  for (const screen of screens) {
    const source = DETAIL_LISTS[screen.id];
    if (!screen.path.includes(":")) {
      out.push(screen);
      continue;
    }
    if (!source) throw new Error(`no way to open ${screen.id}`);
    await page.goto(source.list);
    await settled(page);
    // Emergency dispatch lists only active calls by default; a cleared call is as good a page to open.
    if (screen.id === "dispatch-detail") await page.getByLabel("Status", { exact: true }).selectOption("all");
    const href = await page.locator(`main a[href^="${source.prefix}"]`).first().getAttribute("href", { timeout: 20_000 });
    if (!href) throw new Error(`${screen.id}: the list has no row to open`);
    out.push({ ...screen, path: href });
  }
  return out;
}

/** Whether the page scrolls sideways, and which visible content lies outside the viewport without being inside its own scroll container. */
async function reflowReport(page: Page): Promise<{ pageScroll: boolean; offenders: string[] }> {
  return page.evaluate(() => {
    const width = window.innerWidth;
    const inScroller = (el: Element) => {
      for (let p = el.parentElement; p; p = p.parentElement) {
        const overflow = getComputedStyle(p).overflowX;
        if (overflow === "auto" || overflow === "scroll") return true;
      }
      return false;
    };
    const offenders: string[] = [];
    document.querySelectorAll("header *, nav *, main *").forEach((el) => {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      // Skip links are placed off screen on purpose and come into view when focused.
      if (el.closest(".skip-link, .skip-inline, .sr-only")) return;
      if ((rect.right > width + 1 || rect.left < -1) && !inScroller(el)) {
        const name = (el.getAttribute("aria-label") ?? el.textContent ?? "").trim().slice(0, 30);
        offenders.push(`${el.tagName.toLowerCase()}.${(el.getAttribute("class") ?? "").split(" ")[0]} "${name}" ${Math.round(rect.left)}..${Math.round(rect.right)} of ${width}`);
      }
    });
    return { pageScroll: document.documentElement.scrollWidth > width + 1, offenders: offenders.slice(0, 4) };
  });
}

const OWNER_ORDER = ["supervisor", "operator", "dispatcher", "incident_commander", "field_responder", "auditor", "demo_operator"];
const VIEWPORTS = [
  { name: "laptop 1366x768", width: 1366, height: 768 },
  { name: "projector 1920x1080", width: 1920, height: 1080 },
  { name: "phone 390x844", width: 390, height: 844 },
  { name: "200% zoom (640x512)", width: 640, height: 512 },
  { name: "400% zoom (320x256)", width: 320, height: 256 },
] as const;

test.describe("UX-01 accessibility, every screen, per role", () => {
  for (const role of ROLES) {
    test(`${role}: every screen it may open passes axe (WCAG 2.2 AA), is fully reachable by keyboard with a visible focus indicator, has no keyboard trap, and is a built screen`, async ({ page }) => {
      test.setTimeout(420_000);
      await signIn(page, userFor(role));
      const screens = await concrete(page, screensFor(role));
      const report: { screen: string; tab_stops: number; distinct_stops: number }[] = [];
      for (const screen of screens) {
        await page.goto(screen.path);
        await expect(page.getByRole("heading", { level: 1 }), `${role} ${screen.id}`).toBeVisible({ timeout: 30_000 });
        await settled(page);
        await expect(page.getByText("Not built yet")).toHaveCount(0);
        await expectNoAxeViolations(page, `${role} on ${screen.id}`);
        const header = await tabThrough(page, 14, "skip");
        expect(header[0]?.key, `${role} ${screen.id}: the skip link`).toContain("Skip to main content");
        const stops = [...header, ...(await tabThrough(page, 30, "main"))];
        expect(stops.length, `${role} ${screen.id}: reachable stops`).toBeGreaterThan(6);
        expect(stops.filter((s) => !s.indicator || !s.visible).map((s) => s.key), `${role} ${screen.id}: stops with no focus indicator`).toEqual([]);
        const keys = stops.map((s) => s.at);
        // A date field takes several Tab presses (its segments) while it stays the active element; more than that on one element is a trap.
        const trapped = keys.some((_, i) => i >= 9 && keys.slice(i - 9, i + 1).every((k) => k === keys[i]));
        expect(trapped, `${role} ${screen.id}: focus stuck on one element`).toBe(false);
        report.push({ screen: screen.id, tab_stops: stops.length, distinct_stops: new Set(keys).size });
      }
      recordMetrics(`accessibility.${role}`, { screens_checked: report.length, axe_violations: 0, screens: report });
    });
  }
});

test.describe("UX-04 responsive and reflow", () => {
  const owners = new Map<string, ScreenSpec[]>();
  for (const role of OWNER_ORDER) {
    for (const screen of screensFor(role)) {
      const taken = [...owners.values()].some((list) => list.some((s) => s.id === screen.id));
      if (!taken) owners.set(role, [...(owners.get(role) ?? []), screen]);
    }
  }
  for (const [role, screens] of owners) {
    test(`${role}: its screens fit 1366x768, 1920x1080, a 390x844 phone and 200% and 400% zoom with no page-level horizontal scroll and no clipped or unreachable control`, async ({ page }) => {
      test.setTimeout(420_000);
      await page.setViewportSize({ width: 1366, height: 768 });
      await signIn(page, userFor(role));
      const opened = await concrete(page, screens);
      const results: { screen: string; viewport: string; page_scroll: boolean; offenders: string[] }[] = [];
      for (const screen of opened) {
        await page.setViewportSize({ width: 1366, height: 768 });
        await page.goto(screen.path);
        await expect(page.getByRole("heading", { level: 1 }), `${role} ${screen.id}`).toBeVisible({ timeout: 30_000 });
        await settled(page);
        for (const viewport of VIEWPORTS) {
          await page.setViewportSize({ width: viewport.width, height: viewport.height });
          await page.waitForTimeout(250);
          const found = await reflowReport(page);
          results.push({ screen: screen.id, viewport: viewport.name, page_scroll: found.pageScroll, offenders: found.offenders });
          expect(found.pageScroll, `${role} ${screen.id} at ${viewport.name}: the page scrolls sideways`).toBe(false);
          expect(found.offenders, `${role} ${screen.id} at ${viewport.name}: content outside the viewport that is not in a scroll container`).toEqual([]);
        }
      }
      // The check has to be able to fail: a deliberately wide block is seen.
      await page.setViewportSize({ width: 320, height: 256 });
      await page.evaluate(() => {
        const wide = document.createElement("div");
        wide.style.cssText = "width:1200px;height:10px";
        document.querySelector("main")?.appendChild(wide);
      });
      const probe = await reflowReport(page);
      expect(probe.pageScroll || probe.offenders.length > 0, "the reflow check sees content that overflows").toBe(true);
      // When the navigation is collapsed, "All screens" (the quick bar on a phone) is the way to every screen: it opens by keyboard, lists what
      // the role may open, and Escape closes it.
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(opened[0]!.path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
      const more = page.getByRole("button", { name: "All screens" });
      await expect(more).toBeVisible();
      await more.focus();
      await page.keyboard.press("Enter");
      await expect(more).toHaveAttribute("aria-expanded", "true");
      const nav = page.getByRole("navigation", { name: "Primary" });
      for (const screen of screensFor(role).filter((s) => !s.path.includes(":"))) await expect(nav.getByRole("link", { name: screen.title, exact: true }), `${role}: ${screen.title} in the menu`).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(more).toHaveAttribute("aria-expanded", "false");
      recordMetrics(`responsive.${role}`, { screens: opened.map((s) => s.id), viewports: VIEWPORTS.map((v) => v.name), checks: results.length, page_scroll_failures: results.filter((r) => r.page_scroll).length, offenders: results.filter((r) => r.offenders.length).length });
    });
  }
});

test.describe("UX-04 confirmation dialogs at 400% zoom", () => {
  test("the controls of the longest confirmation dialogs stay reachable, by keyboard and by scrolling inside the dialog, at 320x256", async ({ browser }) => {
    test.setTimeout(180_000);
    const cases: { role: string; path: string; open: string; dialog: string; confirm: string }[] = [
      { role: "operator", path: "/actions/commands", open: "New command", dialog: "Request a command", confirm: "Request command" },
      { role: "supervisor", path: "/handover", open: "Write a handover", dialog: "Write a shift handover", confirm: "Record handover" },
    ];
    for (const item of cases) {
      const context = await browser.newContext({ viewport: { width: 320, height: 256 } });
      const page = await context.newPage();
      await signIn(page, userFor(item.role));
      await page.goto(item.path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
      await settled(page);
      const opener = page.getByRole("button", { name: item.open });
      await opener.scrollIntoViewIfNeeded();
      await opener.click();
      const dialog = page.getByRole("dialog", { name: item.dialog });
      await expect(dialog).toBeVisible();
      const confirm = dialog.getByRole("button", { name: item.confirm });
      await confirm.scrollIntoViewIfNeeded();
      await expect(confirm, `${item.dialog}: the confirm control`).toBeInViewport();
      const cancel = dialog.getByRole("button", { name: "Cancel" });
      await cancel.scrollIntoViewIfNeeded();
      await expect(cancel, `${item.dialog}: cancel`).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${item.dialog}: no sideways page scroll`).toBe(true);
      // Tab reaches the confirm control from Cancel without leaving the dialog.
      await cancel.focus();
      let reached = false;
      for (let i = 0; i < 6 && !reached; i += 1) {
        await page.keyboard.press("Tab");
        reached = await page.evaluate((label) => (document.activeElement?.textContent ?? "").trim() === label, item.confirm);
        expect(await page.evaluate(() => Boolean(document.activeElement?.closest("dialog"))), `${item.dialog}: focus stays inside`).toBe(true);
      }
      expect(reached, `${item.dialog}: Tab reaches ${item.confirm}`).toBe(true);
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      await context.close();
    }
    recordMetrics("dialogs_400_percent_zoom", { dialogs_checked: cases.map((c) => c.dialog), controls_reachable: true });
  });
});

test.describe("UX-01 reduced motion", () => {
  test("with reduced motion requested no animation or transition longer than a blink runs on any screen; without the request the same check does see motion", async ({ page }) => {
    test.setTimeout(300_000);
    const motion = () =>
      page.evaluate(() => {
        const seconds = (value: string) => Math.max(0, ...value.split(",").map((part) => (part.trim().endsWith("ms") ? parseFloat(part) / 1000 : parseFloat(part) || 0)));
        const found: string[] = [];
        document.querySelectorAll("*").forEach((el) => {
          const style = getComputedStyle(el);
          if (style.animationName !== "none" && seconds(style.animationDuration) > 0.05) found.push(`${el.tagName.toLowerCase()}.${(el.getAttribute("class") ?? "").split(" ")[0]} animation ${style.animationName}`);
          if (seconds(style.transitionDuration) > 0.05) found.push(`${el.tagName.toLowerCase()}.${(el.getAttribute("class") ?? "").split(" ")[0]} transition ${style.transitionDuration}`);
          if (style.scrollBehavior === "smooth") found.push(`${el.tagName.toLowerCase()} smooth scrolling`);
        });
        return found.slice(0, 5);
      });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await signIn(page, userFor("supervisor"));
    const screens = await concrete(page, screensFor("supervisor"));
    for (const screen of screens) {
      await page.goto(screen.path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
      await settled(page);
      expect(await motion(), `${screen.id} with reduced motion`).toEqual([]);
    }
    await page.evaluate(() => {
      const spinner = document.createElement("span");
      spinner.className = "spinner";
      spinner.id = "probe-spinner";
      document.body.appendChild(spinner);
    });
    expect(await motion(), "a spinner under reduced motion").toEqual([]);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    expect((await motion()).length, "the same probe sees the spinner when motion is allowed").toBeGreaterThan(0);
    recordMetrics("reduced_motion", { screens_checked: screens.length, animations_running: 0, control_probe_detected_motion_without_the_preference: true });
  });
});
