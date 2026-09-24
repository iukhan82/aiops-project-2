import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { fixture, recordMetrics, settled, signIn, userFor } from "./support";

// LAT-05 (operator UI live update P95 < 2 s) and LOAD-04 (ten concurrent authenticated sessions across roles, no measurable
// regression of LAT-05), measured on the real stack: a real detector reading is written through the platform's own ingestion and the
// time is taken until the device's new reading is rendered in the accessible list of a real Chrome page fed by the live WebSocket.
//
// The clock starts at the reading's own observation time (stamped when the event is created, before ingestion) and stops when the
// page's MutationObserver sees the line rendered, so ingestion, storage, the live push, batching in the browser and rendering are all
// inside the number. Node, Python and the page share one machine and one clock.

const TRIALS = 30;
const DEVICE = "probe-loop-latency";
const LAT05_MS = 2000;
const REGRESSION_ALLOWANCE_MS = 250;

const percentile = (values: number[], p: number): number => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))]!;
};

async function watch(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __seen?: Record<string, number> };
    if (w.__seen) return;
    w.__seen = {};
    const look = (text: string) => {
      const found = /latest: Vehicle count (9\d\d)/.exec(text);
      if (found && w.__seen && w.__seen[found[1]!] === undefined) w.__seen[found[1]!] = Date.now();
    };
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === "attributes") look((record.target as Element).getAttribute("aria-label") ?? "");
        else look(record.target.textContent ?? "");
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["aria-label"] });
  });
}

async function measure(page: Page, first: number, trials: number): Promise<number[]> {
  const latencies: number[] = [];
  for (let i = 0; i < trials; i += 1) {
    const count = first + i;
    const event = fixture<{ observation_time: string }>("loop-event", "--device", DEVICE, "--count", String(count));
    const started = Date.parse(event.observation_time);
    try {
      await expect.poll(() => page.evaluate((c) => (window as unknown as { __seen: Record<string, number> }).__seen[c] ?? null, String(count)), { timeout: 15_000, intervals: [50] }).not.toBeNull();
    } catch (error) {
      const row = await page.getByRole("row").filter({ hasText: DEVICE }).innerText().catch(() => "row not found");
      const state = await page.locator("[data-feed-state]").first().getAttribute("data-feed-state").catch(() => "no feed state");
      throw new Error(`trial ${i} (count ${count}, observation ${event.observation_time}) never appeared; row: ${row.replace(/\s+/g, " ")}; feed: ${state}; seen: ${JSON.stringify(await page.evaluate(() => (window as unknown as { __seen: unknown }).__seen))}`, { cause: error });
    }
    const seen = await page.evaluate((c) => (window as unknown as { __seen: Record<string, number> }).__seen[c]!, String(count));
    latencies.push(seen - started);
    await page.waitForTimeout(400);
  }
  return latencies;
}

function collectApiTimes(page: Page, into: number[]): void {
  page.on("requestfinished", (request) => {
    if (!request.url().includes("/api/v1/") || request.url().includes("/live")) return;
    const timing = request.timing();
    if (timing.responseEnd >= 0) into.push(timing.responseEnd);
  });
}

const SESSIONS: { role: string; nth: number; path: string }[] = [
  { role: "supervisor", nth: 0, path: "/actions/commands" },
  { role: "dispatcher", nth: 0, path: "/dispatch" },
  { role: "incident_commander", nth: 0, path: "/map" },
  { role: "field_responder", nth: 0, path: "/field" },
  { role: "auditor", nth: 0, path: "/operations" },
  { role: "demo_operator", nth: 0, path: "/map" },
  { role: "supervisor", nth: 1, path: "/operations" },
  { role: "operator", nth: 1, path: "/analytics/corridors" },
  { role: "operator", nth: 0, path: "/map" },
];

test("LAT-05 and LOAD-04: the live update P95 stays under 2 s at nominal load and with ten concurrent authenticated sessions across roles, with no measurable regression", async ({ page, browser }) => {
  test.setTimeout(540_000);
  // A detector of its own: the demo replay drives every recorded detector and would overwrite the reading being timed within a second.
  fixture("probe-device", "--id", DEVICE);
  const baselineApi: number[] = [];
  collectApiTimes(page, baselineApi);
  await signIn(page, userFor("operator"));
  await page.goto("/map?view=list");
  await expect(page.getByRole("table", { name: /Every element the map shows/ })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("[data-feed-state='connected']")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("checkbox", { name: "Devices" }).check();
  await expect(page.getByRole("row").filter({ hasText: DEVICE })).toBeVisible({ timeout: 30_000 });
  await settled(page);
  await watch(page);

  // nominal load: this one session
  baselineApi.length = 0;
  const nominal = await measure(page, 900, TRIALS);
  const nominalApi = [...baselineApi];

  // ten concurrent authenticated sessions: this one plus nine more, each on its role's own screen
  const contexts: BrowserContext[] = [];
  const loadedApi: number[] = [];
  collectApiTimes(page, loadedApi);
  const others: Page[] = [];
  for (const session of SESSIONS) {
    const context = await browser.newContext();
    contexts.push(context);
    const other = await context.newPage();
    collectApiTimes(other, loadedApi);
    await signIn(other, userFor(session.role, session.nth));
    await other.goto(session.path);
    await expect(other.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
    others.push(other);
  }
  await page.waitForTimeout(5_000);
  loadedApi.length = 0;
  const loaded = await measure(page, 930, TRIALS);
  const stillHealthy: string[] = [];
  for (const [index, other] of others.entries()) {
    await expect(other.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(other.locator("[data-feed-state='offline']")).toHaveCount(0);
    stillHealthy.push(`${SESSIONS[index]!.role}:${SESSIONS[index]!.path}`);
  }
  const loadedApiSnapshot = [...loadedApi];
  for (const context of contexts) await context.close();

  const summary = (values: number[]) => ({ n: values.length, p50_ms: Math.round(percentile(values, 50)), p95_ms: Math.round(percentile(values, 95)), max_ms: Math.round(Math.max(...values)) });
  const nominalStats = summary(nominal);
  const loadedStats = summary(loaded);
  const nominalApiStats = summary(nominalApi);
  const loadedApiStats = summary(loadedApiSnapshot);
  recordMetrics("LAT-05", {
    target: "operator UI live update P95 < 2 s, backend event to rendered UI change, nominal load",
    method: "a real loop-detector count written through the platform's ingestion; time from the event's observation time to the device's new reading rendered in the map's accessible list in a real Chrome page fed by the live WebSocket",
    device: DEVICE,
    nominal_load: { sessions: 1, ...nominalStats },
    met: nominalStats.p95_ms < LAT05_MS,
    samples_ms: nominal.map(Math.round),
  });
  recordMetrics("LOAD-04", {
    target: "ten concurrent authenticated sessions across roles without measurable UI latency regression (LAT-05)",
    sessions: 1 + SESSIONS.length,
    roles: ["operator", ...SESSIONS.map((s) => s.role)].filter((r, i, all) => all.indexOf(r) === i),
    screens: ["/map (operator)", ...SESSIONS.map((s) => `${s.path} (${s.role})`)],
    live_update_under_load: loadedStats,
    live_update_nominal: nominalStats,
    regression_allowance_ms: REGRESSION_ALLOWANCE_MS,
    live_update_p95_change_ms: loadedStats.p95_ms - nominalStats.p95_ms,
    api_request_time_nominal: nominalApiStats,
    api_request_time_under_load: loadedApiStats,
    sessions_still_healthy: stillHealthy,
    met: loadedStats.p95_ms < LAT05_MS && loadedStats.p95_ms - nominalStats.p95_ms <= REGRESSION_ALLOWANCE_MS,
    samples_ms: loaded.map(Math.round),
  });

  expect(nominalStats.p95_ms, "LAT-05 at nominal load").toBeLessThan(LAT05_MS);
  expect(loadedStats.p95_ms, "LAT-05 with ten sessions").toBeLessThan(LAT05_MS);
  expect(loadedStats.p95_ms - nominalStats.p95_ms, "P95 regression with ten sessions").toBeLessThanOrEqual(REGRESSION_ALLOWANCE_MS);
  expect(loadedApiStats.n, "the other sessions really were calling the API").toBeGreaterThan(20);
});
