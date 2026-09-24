import { expect, test, type Page } from "@playwright/test";
import { expectNoAxeViolations, signIn, userFor } from "./support";

async function open(page: Page, path: string, heading: string, role = "operator"): Promise<void> {
  await signIn(page, userFor(role));
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible({ timeout: 20_000 });
}

test.describe("corridor analytics (P08.06)", () => {
  test("shows the latest complete window with units, truth label and freshness, and a chart of measured and forecast values", async ({ page }) => {
    await open(page, "/analytics/corridors", "Corridor analytics");
    const latest = page.locator("#latest-window");
    await expect(latest).toContainText("Travel time", { timeout: 20_000 });
    await expect(latest).toContainText("km/h");
    await expect(latest).toContainText("Inferred");
    await expect(latest).toContainText(/Fresh|Stale/);
    const chart = page.getByTestId("line-chart");
    await expect(chart.getByRole("img")).toHaveAttribute("aria-label", /Travel time on corridor-a east\. \d+ points from/);
    await expect(chart.getByRole("list", { name: "Chart legend" })).toContainText("Measured");
    await expect(chart.getByRole("list", { name: "Chart legend" })).toContainText(/Forecast 15 min ahead/);
    await expect(chart.getByRole("list", { name: "Chart legend" })).toContainText("80% prediction interval (nominal)");
  });

  test("names the model behind every horizon and says when the baseline is served instead of the trained model", async ({ page }) => {
    await open(page, "/analytics/corridors", "Corridor analytics");
    const source = page.locator("#forecast-source");
    await expect(source).toContainText("5 min", { timeout: 20_000 });
    await expect(source).toContainText("30 min");
    await expect(source).toContainText(/baseline|trained model/);
    await expect(source).toContainText("Predicted");
  });

  test("the chart has a table alternative carrying the same values", async ({ page }) => {
    await open(page, "/analytics/corridors", "Corridor analytics");
    const chart = page.getByTestId("line-chart");
    await expect(chart.getByRole("img")).toBeVisible({ timeout: 20_000 });
    const points = Number(/(\d+) points/.exec((await chart.getByRole("img").getAttribute("aria-label")) ?? "")?.[1]);
    await chart.getByRole("button", { name: "Show as table" }).click();
    const table = chart.getByRole("table", { name: /the same values as the chart/ });
    await expect(table).toBeVisible();
    await expect(table.locator("tbody tr")).toHaveCount(points);
    await expect(table.getByRole("columnheader", { name: /Measured/ })).toBeVisible();
    await expect(table.getByRole("columnheader", { name: /Time \(UTC\)/ })).toBeVisible();
    await chart.getByRole("button", { name: "Show as chart" }).click();
    await expect(chart.getByRole("img")).toBeVisible();
  });

  test("the keyboard moves a crosshair through the points and the value is written out", async ({ page }) => {
    await open(page, "/analytics/corridors", "Corridor analytics");
    const svg = page.getByTestId("line-chart").getByRole("img");
    await svg.focus();
    const selected = page.getByTestId("chart-selected");
    const latest = await selected.innerText();
    expect(latest).toMatch(/\d\d:\d\d UTC: Measured/);
    await page.keyboard.press("ArrowLeft");
    const previous = await selected.innerText();
    expect(previous).not.toEqual(latest);
    await page.keyboard.press("Home");
    const first = await selected.innerText();
    await page.keyboard.press("End");
    expect(await selected.innerText()).not.toEqual(first);
  });

  test("a metric the forecast package does not predict is drawn with measured values only, and says why", async ({ page }) => {
    await open(page, "/analytics/corridors", "Corridor analytics");
    await page.getByLabel("Metric").selectOption("speed_m_s");
    const chart = page.getByTestId("line-chart");
    await expect(chart.getByRole("img")).toHaveAttribute("aria-label", /Mean speed on corridor-a east/);
    await expect(chart).toContainText("is not one of the quantities the forecast model predicts");
    await expect(chart.getByRole("list", { name: "Chart legend" })).not.toContainText("Forecast");
    await page.getByLabel("Corridor", { exact: true }).selectOption("corridor-b");
    await expect(chart.getByRole("img")).toHaveAttribute("aria-label", /on corridor-b east/);
  });

  test("pausing stops the polling and says so", async ({ page }) => {
    await open(page, "/analytics/corridors", "Corridor analytics");
    await expect(page.locator("[data-feed-state]")).toHaveAttribute("data-feed-state", "connected", { timeout: 20_000 });
    await page.getByRole("banner").getByRole("button", { name: "Pause updates" }).click();
    await expect(page.locator("[data-feed-state]")).toHaveAttribute("data-feed-state", "paused");
    await expect(page.getByText("Paused. The chart is not being updated.")).toBeVisible();
  });

  test("lists detector output for the corridor as candidates, labelled inferred", async ({ page }) => {
    await open(page, "/analytics/corridors", "Corridor analytics");
    await expect(page.locator("#detections")).toBeVisible();
    const rows = page.locator("#detections tbody tr");
    const empty = page.locator("#detections").getByText("The detectors have raised nothing on this corridor");
    await expect.poll(async () => (await rows.count()) + (await empty.count()), { timeout: 20_000 }).toBeGreaterThan(0);
    if ((await rows.count()) > 0) await expect(page.locator("#detections")).toContainText("Inferred");
  });

  test("no accessibility violations, chart and table", async ({ page }) => {
    await open(page, "/analytics/corridors", "Corridor analytics");
    await expect(page.getByTestId("line-chart").getByRole("img")).toBeVisible({ timeout: 20_000 });
    await expectNoAxeViolations(page, "corridor analytics chart");
    await page.getByTestId("line-chart").getByRole("button", { name: "Show as table" }).click();
    await expectNoAxeViolations(page, "corridor analytics table");
  });

  test("a role without analytics.view is refused", async ({ page }) => {
    await signIn(page, userFor("field_responder"));
    await page.goto("/analytics/corridors");
    await expect(page).toHaveURL(/capability=analytics\.view/);
  });
});

test.describe("intersection analytics (P08.06)", () => {
  test("shows the signal groups in words with shape and label, from the controller's latest message", async ({ page }) => {
    await open(page, "/analytics/intersections?intersection=int-a1", "Intersection analytics");
    const panel = page.getByRole("tabpanel");
    await expect(panel).toContainText("Controller", { timeout: 20_000 });
    await expect(panel).toContainText("signal-int-a1");
    await expect(panel.getByRole("list", { name: "Signal groups, in order" }).locator("li").first()).toContainText(/Group 1: (green|red|yellow)/);
    await expect(panel).toContainText(/Raw state/);
    await expect(panel).toContainText("Simulated");
  });

  test("tabs move with the arrow keys and show movements, devices and observations", async ({ page }) => {
    await open(page, "/analytics/intersections?intersection=int-a1", "Intersection analytics");
    await page.getByRole("tab", { name: "Signal" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "Movements" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel")).toContainText(/Crossing detectors|No pedestrian or cycle detector here/);
    await page.keyboard.press("ArrowRight");
    const devices = page.getByRole("table", { name: "Devices registered at int-a1" });
    await expect(devices).toBeVisible();
    await expect(devices).toContainText("signal-int-a1");
    expect(await devices.locator("tbody tr").count()).toBeGreaterThanOrEqual(2);
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("table", { name: /Most recent observations from the devices at int-a1/ })).toBeVisible();
  });

  test("switching intersection keeps the deep link and reloads everything for it", async ({ page }) => {
    await open(page, "/analytics/intersections", "Intersection analytics");
    await page.getByLabel("Intersection", { exact: true }).selectOption("int-b2");
    await expect(page).toHaveURL(/intersection=int-b2/);
    await expect(page.getByRole("tabpanel")).toContainText("signal-int-b2", { timeout: 20_000 });
  });

  test("junction state says plainly when no detector has ever reported for it", async ({ page }) => {
    await open(page, "/analytics/intersections?intersection=int-a1", "Intersection analytics");
    const panel = page.locator("#junction-state");
    await expect(panel).toContainText(/Window|No detector has ever reported/, { timeout: 20_000 });
  });

  test("no accessibility violations", async ({ page }) => {
    await open(page, "/analytics/intersections?intersection=int-a1", "Intersection analytics");
    await expect(page.getByRole("tabpanel")).toContainText("Controller", { timeout: 20_000 });
    await expectNoAxeViolations(page, "intersection analytics");
  });
});

test.describe("device health (P08.06)", () => {
  test("summarises how many devices are reporting, stale or never reported, and lists every device", async ({ page }) => {
    await open(page, "/analytics/devices", "Device health");
    const summary = page.getByRole("group", { name: "Reporting summary" });
    await expect(summary).toContainText("devices registered", { timeout: 20_000 });
    await expect(summary).toContainText("never reported");
    const rows = page.locator("#devices-panel tbody tr");
    expect(await rows.count()).toBeGreaterThan(60);
    await expect(page.locator("#devices-panel")).toContainText("Simulated");
  });

  test("filters by type and reporting state and announces the count", async ({ page }) => {
    await open(page, "/analytics/devices", "Device health");
    await expect(page.locator("#devices-panel tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await page.getByLabel("Device type").selectOption("signal_controller");
    await expect(page.getByText("12 devices shown.")).toBeVisible();
    await page.getByLabel("Device type").selectOption("all");
    await page.getByLabel("Reporting", { exact: true }).selectOption("never");
    const never = page.getByText(/^\d+ devices? shown\.$/);
    await expect(never).toBeVisible();
  });

  test("selecting a device shows its registration, freshness and latest observations", async ({ page }) => {
    await open(page, "/analytics/devices", "Device health");
    await page.getByLabel("Device type").selectOption("signal_controller");
    await page.getByRole("button", { name: "signal-int-a1", exact: true }).click();
    const detail = page.locator("#device-detail");
    await expect(detail).toContainText("signal-int-a1", { timeout: 20_000 });
    await expect(detail).toContainText("Last observation");
    await expect(detail.getByRole("table", { name: /Latest observations from signal-int-a1/ })).toBeVisible();
    await expect(detail.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
  });

  test("sorting the reporting column works from the keyboard", async ({ page }) => {
    await open(page, "/analytics/devices", "Device health");
    const header = page.getByRole("columnheader", { name: /Reporting/ });
    await page.getByRole("button", { name: "Reporting" }).focus();
    await page.keyboard.press("Enter");
    await expect(header).toHaveAttribute("aria-sort", /ascending|descending/);
  });

  test("no accessibility violations", async ({ page }) => {
    await open(page, "/analytics/devices", "Device health");
    await page.getByLabel("Device type").selectOption("signal_controller");
    await page.getByRole("button", { name: "signal-int-a1", exact: true }).click();
    await expect(page.locator("#device-detail tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await expectNoAxeViolations(page, "device health");
  });
});
