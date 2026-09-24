import { expect, test, type Page } from "@playwright/test";
import { expectNoAxeViolations, fixture, signIn, userFor } from "./support";

async function takeCall(page: Page, options: { type?: string; subtype?: string; where?: string; priority?: string } = {}): Promise<string> {
  await page.goto("/dispatch");
  await expect(page.getByRole("heading", { level: 1, name: "Emergency dispatch" })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Take a new call" }).click();
  const dialog = page.getByRole("dialog", { name: "Take a new call" });
  await dialog.getByLabel("Call type").selectOption(options.type ?? "ambulance");
  await dialog.getByLabel(/What is it about/).fill(options.subtype ?? "medical emergency");
  await dialog.getByLabel("Priority").selectOption(options.priority ?? "high");
  await dialog.getByLabel("Where", { exact: true }).selectOption(options.where ?? "int-b2");
  await dialog.getByRole("button", { name: "Take call" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Call, units and routes" })).toBeVisible({ timeout: 20_000 });
  return new URL(page.url()).pathname.split("/").pop()!;
}

async function assign(page: Page, unit: string, origin: string): Promise<void> {
  const box = page.getByRole("group", { name: "Assign a unit" });
  await box.getByLabel("Unit", { exact: true }).selectOption(unit);
  await box.getByLabel("Where the unit is").selectOption(origin);
  await box.getByRole("button", { name: "Assign unit" }).click();
  await expect(page.getByText(new RegExp(`${unit} assigned`)).first()).toBeVisible({ timeout: 20_000 });
}

test.describe("emergency dispatch (P08.07)", () => {
  test("a new call needs its fields, keeps the person on the form when they are missing, and opens the call when taken", async ({ page }) => {
    await signIn(page, userFor("dispatcher"));
    await page.goto("/dispatch");
    await page.getByRole("button", { name: "Take a new call" }).click();
    const dialog = page.getByRole("dialog", { name: "Take a new call" });
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    await dialog.getByRole("button", { name: "Take call" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Fix the highlighted fields");
    await expect(dialog.getByText("Say what the call is about")).toBeVisible();
    await expect(dialog.getByRole("alert")).toBeFocused();
    await dialog.getByLabel(/What is it about/).fill("bad; subtype");
    await dialog.getByRole("button", { name: "Take call" }).click();
    await expect(dialog.getByText("Use letters, numbers, spaces and underscores only.")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("button", { name: "Take a new call" })).toBeFocused();
    const id = await takeCall(page, { subtype: "medical emergency", where: "int-b2" });
    await expect(page.locator("#call-summary")).toContainText("Received");
    await expect(page.locator("#call-summary")).toContainText("Operator entered");
    await expect(page.locator("#call-timeline")).toContainText("dana.rivera");
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
  });

  test("assigning a unit finds route alternatives with ETA and uncertainty, one selected, and the call follows its unit to cleared", async ({ page }) => {
    await signIn(page, userFor("dispatcher"));
    await takeCall(page);
    await assign(page, "ambulance-1", "int-b4");
    const units = page.locator("#units");
    await expect(units).toContainText("ambulance-1");
    const routes = units.getByRole("list", { name: "Route alternatives for ambulance-1" });
    await expect(routes.locator("li")).not.toHaveCount(0);
    await expect(routes.locator('li[data-selected="true"]')).toHaveCount(1);
    await expect(routes.first()).toContainText("plus or minus");
    await expect(routes.first()).toContainText("Predicted");
    await expect(page.locator("#call-summary")).toContainText("Unit assigned");
    if ((await routes.locator("li").count()) > 1) {
      const other = routes.locator('li[data-selected="false"]').first();
      const id = await other.getAttribute("data-route-id");
      await other.getByRole("button", { name: /Select route/ }).click();
      await expect(page.getByText("Route selected. The choice is in the timeline.")).toBeVisible();
      await expect(routes.locator(`li[data-route-id="${id}"]`)).toHaveAttribute("data-selected", "true");
      await expect(routes.locator('li[data-selected="true"]')).toHaveCount(1);
    }
    for (const [button, callStatus] of [
      ["Unit acknowledged", "Unit assigned"],
      ["Unit en route", "En route"],
      ["Unit on scene", "On scene"],
      ["Unit clear", "Cleared"],
    ] as const) {
      await page.getByRole("group", { name: "Move ambulance-1" }).getByRole("button", { name: button }).click();
      await expect(page.locator("#call-summary")).toContainText(callStatus, { timeout: 20_000 });
    }
    await expect(page.getByRole("group", { name: "Assign a unit" })).toHaveCount(0);
    const timeline = page.locator("#call-timeline");
    await expect(timeline).toContainText("On scene to Clear");
    await expect(timeline).toContainText("Oldest first");
    await expect(page.getByRole("group", { name: "Move ambulance-1" })).toHaveCount(0);
  });

  test("a unit whose last position is old is not routed from a guess; saying where it is fixes it", async ({ page }) => {
    await signIn(page, userFor("dispatcher"));
    await takeCall(page, { type: "police", subtype: "traffic collision", where: "int-c3", priority: "medium" });
    fixture("unit-position", "--unit", "police-2", "--intersection", "int-c1", "--age-minutes", "30", "--reset");
    const box = page.getByRole("group", { name: "Assign a unit" });
    await box.getByLabel("Unit", { exact: true }).selectOption("police-2");
    await box.getByRole("button", { name: "Assign unit" }).click();
    await expect(page.getByRole("alert").filter({ hasText: /last reported its position \d+ minutes ago, too long to route from/ })).toBeVisible({ timeout: 20_000 });
    fixture("unit-position", "--unit", "police-2", "--intersection", "int-a3");
    await box.getByRole("button", { name: "Assign unit" }).click();
    await expect(page.getByText("police-2 assigned. Routes were worked out from its last reported position.")).toBeVisible({ timeout: 20_000 });
  });

  test("cancelling a call is a confirmation that needs a reason and cannot be repeated", async ({ page }) => {
    await signIn(page, userFor("dispatcher"));
    await takeCall(page, { type: "fire", subtype: "structure fire", where: "int-a2", priority: "critical" });
    const opener = page.getByRole("button", { name: "Cancel call" });
    await opener.click();
    const dialog = page.getByRole("dialog", { name: "Cancel this call" });
    await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
    await dialog.getByRole("button", { name: "Cancel the call" }).click();
    await expect(dialog.getByText("A reason is required to cancel a call.")).toBeVisible();
    await dialog.getByLabel("Why is it cancelled").fill("Caller withdrew the report");
    await dialog.getByRole("button", { name: "Cancel the call" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Call cancelled");
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(page.locator("#call-summary")).toContainText("Cancelled");
    await expect(page.locator("#call-timeline")).toContainText("Caller withdrew the report");
    await expect(page.getByRole("button", { name: "Cancel call" })).toHaveCount(0);
    await expect(page.getByRole("group", { name: "Assign a unit" })).toHaveCount(0);
  });

  test("the call list shows priority, status and truth label, filters and links to the call", async ({ page }) => {
    await signIn(page, userFor("dispatcher"));
    await takeCall(page, { subtype: "cardiac arrest", where: "int-b3", priority: "critical" });
    await page.goto("/dispatch");
    const table = page.getByRole("table", { name: "Emergency calls" });
    await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await expect(table.locator("tbody tr").first()).toContainText("Critical");
    await expect(table.locator("tbody tr").first()).toContainText("Operator entered");
    await page.getByLabel("Type").selectOption("fire");
    await expect(page.getByText(/^\d+ calls? shown\.$/)).toBeVisible();
    await page.getByLabel("Type").selectOption("all");
    await page.getByLabel("Status").selectOption("cleared");
    await expect(page.getByText(/incidents? shown|calls? shown/).first()).toBeVisible();
  });

  test("an operator can read the dispatch screens but has no way to take a call, assign or move a unit", async ({ page }) => {
    await signIn(page, userFor("operator"));
    await page.goto("/dispatch");
    await expect(page.getByRole("heading", { level: 1, name: "Emergency dispatch" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Take a new call" })).toHaveCount(0);
    const link = page.getByRole("table", { name: "Emergency calls" }).locator("tbody a").first();
    await link.click();
    await expect(page.getByRole("heading", { level: 1, name: "Call, units and routes" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("group", { name: "Assign a unit" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Cancel call" })).toHaveCount(0);
  });

  test("the field view is read-only on a phone: the call, the route with its ETA, the steps and what changed, and no control that acts", async ({ browser }) => {
    const dispatcher = await browser.newContext();
    const desk = await dispatcher.newPage();
    await signIn(desk, userFor("dispatcher"));
    await takeCall(desk, { subtype: "field responder check", where: "int-b2", priority: "critical" });
    fixture("unit-position", "--unit", "ambulance-2", "--intersection", "int-b4");
    await assign(desk, "ambulance-2", "");
    await desk.getByRole("group", { name: "Move ambulance-2" }).getByRole("button", { name: "Unit acknowledged" }).click();
    await expect(desk.locator("#call-timeline")).toContainText("Assigned to Acknowledged");

    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
    const page = await phone.newPage();
    await signIn(page, userFor("field_responder"));
    await page.goto("/field");
    await expect(page.getByRole("heading", { level: 1, name: "Field view" })).toBeVisible({ timeout: 20_000 });
    await expect(page.locator("#field-call")).toContainText(/Ambulance|Fire|Police/, { timeout: 20_000 });
    await expect(page.locator("#field-route")).toContainText("Selected", { timeout: 20_000 });
    await expect(page.locator("#field-route")).toContainText("plus or minus");
    await expect(page.locator("#field-route")).toContainText("Predicted");
    await expect(page.locator("#field-changes")).not.toContainText("Nothing has changed yet.");
    await expect(page.getByRole("button", { name: /Approve|Request|Execute|Assign|Cancel|Take|Select route|Unit / })).toHaveCount(0);
    const quick = page.getByRole("navigation", { name: "Quick navigation" });
    await expect(quick).toBeVisible();
    for (const target of await quick.getByRole("link").all()) {
      const box = await target.boundingBox();
      expect(box!.height, "touch target height").toBeGreaterThanOrEqual(44);
    }
    expect(await quick.getByRole("link").count()).toBeLessThanOrEqual(3);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, "no horizontal page scroll at 390px").toBeLessThanOrEqual(1);
    await expectNoAxeViolations(page, "field view");
    await phone.close();
    await dispatcher.close();
  });

  test("no accessibility violations on the list, the call and the new-call dialog", async ({ page }) => {
    await signIn(page, userFor("dispatcher"));
    await page.goto("/dispatch");
    await expect(page.getByRole("table", { name: "Emergency calls" })).toBeVisible({ timeout: 20_000 });
    await expectNoAxeViolations(page, "dispatch list");
    await page.getByRole("button", { name: "Take a new call" }).click();
    await expectNoAxeViolations(page, "new call dialog");
    await page.keyboard.press("Escape");
    await takeCall(page, { subtype: "axe check" });
    await assign(page, "ambulance-1", "int-b4");
    await expectNoAxeViolations(page, "dispatch detail");
  });
});
