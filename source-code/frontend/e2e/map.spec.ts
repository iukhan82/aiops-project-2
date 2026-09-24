import { expect, test, type Page } from "@playwright/test";
import { expectNoAxeViolations, signIn, userFor } from "./support";

async function openMap(page: Page, role = "operator", query = ""): Promise<void> {
  await signIn(page, userFor(role));
  await page.goto(`/map${query}`);
  await expect(page.getByRole("heading", { level: 1, name: /Live operations map/ })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('[data-item-id^="segment:"]').first()).toBeVisible({ timeout: 20_000 });
}

const feedState = (page: Page) => page.locator("[data-feed-state]");

test.describe("live operations map (P08.05)", () => {
  test("draws the whole network, says what it knows, and the live feed connects", async ({ page }) => {
    await openMap(page);
    await expect(page.locator('[data-item-id^="segment:"]')).toHaveCount(26);
    await expect(page.locator('[data-item-id^="intersection:"]')).toHaveCount(12);
    await expect(feedState(page)).toHaveAttribute("data-feed-state", "connected", { timeout: 20_000 });
    await expect(page.getByText("newest value")).toBeVisible();
    await expect(page.getByRole("region", { name: "Map legend" })).toContainText("Solid: flowing");
    const speeds = await page.locator('[data-item-id^="segment:"]').evaluateAll((els) => els.filter((el) => /km\/h/.test(el.getAttribute("aria-label") ?? "")).length);
    expect(speeds, "segments with a measured speed").toBeGreaterThan(5);
  });

  test("selecting a segment shows value, unit, observed time, truth label and freshness", async ({ page }) => {
    await openMap(page);
    await page.locator('[data-item-id="segment:int-a1_int-a2"]').click();
    const panel = page.getByTestId("selection-panel");
    await expect(panel).toContainText("int-a1 to int-a2");
    await expect(panel).toContainText("km/h");
    await expect(panel).toContainText(/\d\d:\d\d:\d\d UTC/);
    await expect(panel).toContainText("Simulated");
    await expect(panel).toContainText(/Fresh|Stale|Unknown/);
    await expect(panel).toContainText("corridor-a");
  });

  test("the list carries every element with status, freshness and truth label, sorts and filters, and keeps the selection", async ({ page }) => {
    await openMap(page);
    await page.locator('[data-item-id="segment:int-a2_int-a3"]').click();
    await page.getByRole("radio", { name: "List" }).click();
    await expect(page).toHaveURL(/view=list/);
    const table = page.getByRole("table", { name: /Every element the map shows/ });
    await expect(table).toBeVisible();
    await expect(page.getByTestId("selection-panel")).toContainText("int-a2 to int-a3");
    await expect(table.locator('tbody tr[aria-current="true"]')).toHaveCount(1);
    await expect(table.getByRole("columnheader", { name: /Freshness/ })).toBeVisible();
    await expect(table.getByRole("columnheader", { name: /Truth label/ })).toBeVisible();
    await page.getByLabel("Kind").selectOption("segment");
    await expect(page.getByText("26 elements shown.")).toBeVisible();
    await page.getByRole("button", { name: "Element" }).click();
    await expect(table.getByRole("columnheader", { name: /Element/ })).toHaveAttribute("aria-sort", "ascending");
    await page.getByRole("button", { name: "Element" }).click();
    await expect(table.getByRole("columnheader", { name: /Element/ })).toHaveAttribute("aria-sort", "descending");
    await page.getByLabel("Search").fill("int-b3");
    await expect(page.getByText(/^\d+ elements? shown\.$/)).toBeVisible();
    await expect(table.locator("tbody tr")).not.toHaveCount(26);
    await page.getByRole("radio", { name: "Map" }).click();
    await expect(page.getByTestId("selection-panel")).toContainText("int-a2 to int-a3");
    await expect(page.locator('[data-item-id="segment:int-a2_int-a3"]')).toHaveAttribute("aria-pressed", "true");
  });

  test("keyboard: the canvas zooms and pans, elements are reachable with Tab and selected with Enter, Escape clears", async ({ page }) => {
    await openMap(page);
    const canvas = page.getByTestId("map-canvas");
    await canvas.focus();
    const before = await canvas.getAttribute("viewBox");
    await page.keyboard.press("+");
    const zoomed = await canvas.getAttribute("viewBox");
    expect(zoomed).not.toEqual(before);
    await page.keyboard.press("ArrowRight");
    expect(await canvas.getAttribute("viewBox")).not.toEqual(zoomed);
    await page.keyboard.press("0");
    expect(await canvas.getAttribute("viewBox")).toEqual(before);
    await page.keyboard.press("Tab");
    await expect(page.locator("[data-item-id]:focus")).toHaveCount(1);
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("selection-panel")).toHaveAttribute("data-selected-id", /.+/);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("selection-panel")).not.toHaveAttribute("data-selected-id", /.+/);
  });

  test("the Map/List control is one Tab stop and moves with the arrow keys", async ({ page }) => {
    await openMap(page);
    await page.getByRole("radio", { name: "Map" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("radio", { name: "List" })).toBeChecked();
    await expect(page).toHaveURL(/view=list/);
    await page.keyboard.press("Home");
    await expect(page.getByRole("radio", { name: "Map" })).toBeChecked();
  });

  test("layers show and hide elements, and a hidden layer stays in the list", async ({ page }) => {
    await openMap(page);
    const incidents = page.locator('[data-item-id^="incident:"]');
    await expect.poll(async () => incidents.count(), { timeout: 20_000 }).toBeGreaterThan(0);
    await page.getByRole("checkbox", { name: "Incidents" }).uncheck();
    await expect(incidents).toHaveCount(0);
    await page.getByRole("checkbox", { name: "Traffic" }).uncheck();
    await expect(page.locator('[data-item-id^="segment:"]')).toHaveCount(0);
    await page.getByRole("checkbox", { name: "Devices" }).check();
    await expect.poll(async () => page.locator('[data-item-id^="device:"]').count()).toBeGreaterThan(10);
    await page.getByRole("radio", { name: "List" }).click();
    await page.getByLabel("Kind").selectOption("incident");
    await expect(page.getByRole("table").locator("tbody tr").first()).toBeVisible();
  });

  test("pause stops updates and says so; resume catches up", async ({ page }) => {
    await openMap(page);
    await expect(feedState(page)).toHaveAttribute("data-feed-state", "connected", { timeout: 20_000 });
    await page.getByRole("banner").getByRole("button", { name: "Pause updates" }).click();
    await expect(feedState(page)).toHaveAttribute("data-feed-state", "paused");
    await expect(page.getByText("Updates are paused. What you see was current when you paused")).toBeVisible();
    await expect(page.locator(".map-canvas.not-live")).toHaveCount(1);
    await page.getByRole("button", { name: "Resume updates" }).first().click();
    await expect(feedState(page)).toHaveAttribute("data-feed-state", "connected", { timeout: 20_000 });
    await expect(page.locator(".map-canvas.not-live")).toHaveCount(0);
  });

  test("replay is labelled not live everywhere and only replays what history can support", async ({ page }) => {
    await openMap(page);
    await page.getByRole("button", { name: "Replay recent history" }).click();
    await expect(page.getByText("Replay, not live.").first()).toBeVisible();
    await expect(page.locator(".map-canvas.not-live")).toHaveCount(1);
    await expect(page.locator('[data-item-id^="incident:"]')).toHaveCount(0);
    await expect(page.locator("[data-feed-state]")).toHaveCount(0);
    await expect(page.locator('[data-item-id^="segment:"]')).toHaveCount(26);
    const slider = page.getByLabel("Replay time");
    const first = await slider.inputValue();
    await slider.focus();
    await page.keyboard.press("ArrowLeft");
    expect(await slider.inputValue()).not.toEqual(first);
    await page.getByRole("button", { name: "Return to live" }).click();
    await expect(feedState(page)).toHaveAttribute("data-feed-state", "connected", { timeout: 20_000 });
  });

  test("a dropped connection is shown as reconnecting and then resumes from the last event it received, with no gap", async ({ page }) => {
    const sinces: string[] = [];
    const lastReceived: string[] = [];
    let current: { close: () => void } | null = null;
    await page.routeWebSocket(/\/api\/v1\/live/, (ws) => {
      sinces.push(new URL(ws.url()).searchParams.get("since") ?? "");
      const server = ws.connectToServer();
      current = ws;
      ws.onMessage((message) => server.send(message));
      server.onMessage((message) => {
        try {
          lastReceived.push((JSON.parse(String(message)) as { received_at: string }).received_at);
        } catch {
          /* not an event */
        }
        ws.send(message);
      });
    });
    await openMap(page);
    await expect(feedState(page)).toHaveAttribute("data-feed-state", "connected", { timeout: 20_000 });
    await expect.poll(() => lastReceived.length, { timeout: 60_000 }).toBeGreaterThan(0);
    const seen = lastReceived[lastReceived.length - 1]!;
    const before = sinces.length;
    current!.close();
    await expect.poll(() => sinces.length, { timeout: 20_000 }).toBeGreaterThan(before);
    const since = sinces[before]!;
    expect(new Date(since).getTime(), "the new connection asks for everything after the last received_at").toBeGreaterThanOrEqual(new Date(seen).getTime());
    expect(new Date(since).getTime()).toBeLessThan(new Date(seen).getTime() + 60_000);
    await expect(feedState(page)).toHaveAttribute("data-feed-state", "connected", { timeout: 20_000 });
  });

  test("roles: incident and emergency layers and the queue appear only for roles that may see them", async ({ page }) => {
    await openMap(page, "demo_operator");
    await expect(page.getByRole("checkbox", { name: "Incidents" })).toHaveCount(0);
    await expect(page.getByRole("checkbox", { name: "Emergency" })).toHaveCount(0);
    await expect(page.getByTestId("incident-queue")).toHaveCount(0);
    await expect(page.getByRole("checkbox", { name: "Traffic" })).toBeVisible();
  });

  test("the operator sees the queue with critical incidents first, linked to their detail", async ({ page }) => {
    await openMap(page);
    const queue = page.getByTestId("incident-queue");
    await expect(queue).toBeVisible();
    await expect.poll(async () => queue.locator("li").count(), { timeout: 30_000 }).toBeGreaterThan(0);
    await expect(queue.locator("li a").first()).toHaveAttribute("href", /\/incidents\/[0-9a-f-]{36}/);
  });

  test("the wall display variant has no controls", async ({ page }) => {
    await openMap(page, "supervisor", "?wall=1");
    await expect(page.getByRole("heading", { level: 1, name: "Live operations map, wall display" })).toBeVisible();
    await expect(page.getByRole("radio", { name: "List" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Replay recent history" })).toHaveCount(0);
  });

  test("no accessibility violations on the map or the list", async ({ page }) => {
    await openMap(page);
    await page.locator('[data-item-id="segment:int-a1_int-a2"]').click();
    await expectNoAxeViolations(page, "map");
    await page.getByRole("radio", { name: "List" }).click();
    await expect(page.getByRole("table")).toBeVisible();
    await expectNoAxeViolations(page, "map list");
  });
});
