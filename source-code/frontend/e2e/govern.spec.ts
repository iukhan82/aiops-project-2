import { expect, test, type Browser, type Page } from "@playwright/test";
import { expectNoAxeViolations, fixture, nameOf, signIn, userFor } from "./support";

async function asPerson(browser: Browser, role: string, path: string, nth = 0): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, userFor(role, nth));
  await page.goto(path);
  return page;
}

const sign = (user: string, path: string, body: object) =>
  fixture<{ status: number; body: Record<string, unknown> }>("as-user", "--user", user, "--method", "POST", "--path", path, "--json", JSON.stringify(body));

test.describe("audit trail (P08.09)", () => {
  test("lists what people and services did newest first, with who, what and the result, and refusals are in it", async ({ page }) => {
    const refused = sign(userFor("field_responder"), "/api/v1/commands", { action_type: "variable_message_sign", target_entity_id: "int-a1_int-a2", message: "x", idempotency_key: `e2e-audit-${Date.now()}` });
    expect(refused.status).toBe(403);
    await signIn(page, userFor("auditor"));
    await page.goto("/audit");
    await expect(page.getByRole("heading", { level: 1, name: "Audit trail" })).toBeVisible({ timeout: 20_000 });
    const table = page.getByRole("table", { name: "Audit trail" });
    await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    const times = await table.locator("tbody tr > th").allInnerTexts();
    expect(times.length).toBeGreaterThan(5);
    expect(times.map((t) => t.trim())).toEqual([...times.map((t) => t.trim())].sort().reverse());
    for (const header of ["Time", "Who", "What", "Concerning", "Result", "Recorded in", "Detail"]) await expect(table.getByRole("columnheader", { name: new RegExp(header) })).toBeVisible();
    await expect(table).toContainText("Allowed");
    await expect(page.getByText("Nothing on this screen can be edited or deleted.")).toBeVisible();
    await expectNoAxeViolations(page, "audit trail");
  });

  test("filters by who, what it concerns, the result and time; a start after the end is refused; focus stays on the control that was used", async ({ page }) => {
    const field = userFor("field_responder");
    sign(field, "/api/v1/commands", { action_type: "variable_message_sign", target_entity_id: "int-a1_int-a2", message: "x", idempotency_key: `e2e-audit-${Date.now()}` });
    const made = sign(userFor("operator"), "/api/v1/commands", { action_type: "variable_message_sign", target_entity_id: "int-a1_int-a2", message: `audit ${Date.now()}`, idempotency_key: `e2e-audit-cmd-${Date.now()}` });
    const commandId = String(made.body.command_id);
    await signIn(page, userFor("auditor"));
    await page.goto("/audit");
    const table = page.getByRole("table", { name: "Audit trail" });
    await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });

    await page.getByLabel("Who", { exact: true }).fill(field);
    await page.getByLabel("Result", { exact: true }).selectOption("denied");
    const apply = page.getByRole("button", { name: "Apply filters" });
    await apply.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText(/records? loaded for these filters/)).toBeVisible({ timeout: 20_000 });
    await expect(apply).toBeFocused();
    const rows = await table.locator("tbody tr").allInnerTexts();
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row).toContain(field);
      expect(row).toContain("Refused");
    }
    await expect(table.locator("tbody tr").first()).toContainText("Tried POST /api/v1/commands");

    await page.getByRole("button", { name: "Clear filters" }).click();
    await page.getByLabel("Identifier", { exact: true }).fill(commandId);
    await apply.click();
    await expect(page.getByText(/records? loaded for these filters/)).toBeVisible({ timeout: 20_000 });
    await expect(table).toContainText("Requested a command");
    await expect(table).toContainText("Command moved to requested");
    await expect(table).toContainText("Action through the API");
    await expect(table).toContainText("Command state history");
    await expect(table.getByRole("link", { name: /Command / }).first()).toHaveAttribute("href", `/actions/commands/${commandId}`);

    await page.getByRole("button", { name: "Clear filters" }).click();
    await page.getByLabel("From (UTC)").fill("2999-01-01T00:00");
    await apply.click();
    await expect(page.getByRole("heading", { name: "No record matches" })).toBeVisible({ timeout: 20_000 });
    await expectNoAxeViolations(page, "audit trail with no match");

    await page.getByLabel("From (UTC)").fill("2026-09-21T10:00");
    await page.getByLabel("Until (UTC)").fill("2026-09-21T09:00");
    await apply.click();
    await expect(page.getByText("The start is after the end. Swap them, or clear one.")).toBeVisible();
    await expect(page.getByLabel("Until (UTC)")).toHaveAttribute("aria-invalid", "true");

    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByLabel("Who", { exact: true })).toHaveValue("");
  });

  test("'Only this' narrows the trail to one entity, and a linked entity opens its own screen", async ({ page }) => {
    const made = sign(userFor("operator"), "/api/v1/commands", { action_type: "variable_message_sign", target_entity_id: "int-a1_int-a2", message: `only ${Date.now()}`, idempotency_key: `e2e-audit-only-${Date.now()}` });
    const commandId = String(made.body.command_id);
    await signIn(page, userFor("auditor"));
    await page.goto("/audit");
    const table = page.getByRole("table", { name: "Audit trail" });
    await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await table.getByRole("button", { name: `Show only Command ${commandId.slice(0, 8)}` }).first().click();
    await expect(page.getByLabel("Identifier", { exact: true })).toHaveValue(commandId, { timeout: 20_000 });
    await expect(page.getByLabel("Concerning", { exact: true })).toHaveValue("command");
    await expect(page.getByText(/records? loaded for these filters/)).toBeVisible({ timeout: 20_000 });
    for (const row of await table.locator("tbody tr").allInnerTexts()) expect(row).toContain(commandId.slice(0, 8));
    await table.getByRole("link", { name: /Command / }).first().click();
    await expect(page.getByRole("heading", { level: 1, name: "Command detail" })).toBeVisible({ timeout: 20_000 });
  });

  test("nothing on the screen can change a record, and a role without audit.view is told which capability it lacks", async ({ page, browser }) => {
    await signIn(page, userFor("auditor"));
    await page.goto("/audit");
    await expect(page.getByRole("table", { name: "Audit trail" }).locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    const buttons = (await page.getByRole("main").getByRole("button").allInnerTexts()).map((t) => t.trim());
    for (const label of buttons) expect(label, "only reading, filtering and sorting controls").toMatch(/^(Refresh|Apply filters|Clear filters|Only this|Load more|(Time|What|Concerning|Result|Recorded in|Who)( [▲▼])?)$/);
    const operator = await asPerson(browser, "operator", "/audit");
    await expect(operator.getByRole("heading", { level: 1, name: "Not permitted" })).toBeVisible({ timeout: 20_000 });
    await expect(operator.getByText("audit.view")).toBeVisible();
    await expect(operator.getByRole("link", { name: "Audit trail" })).toHaveCount(0);
    await operator.context().close();
  });
});

test.describe("platform status (P08.09)", () => {
  test("shows real probes: dependencies, worker heartbeats, freshness per source and ingestion measured from what arrived", async ({ page }) => {
    await signIn(page, userFor("supervisor"));
    await page.goto("/operations");
    await expect(page.getByRole("heading", { level: 1, name: "Platform status" })).toBeVisible({ timeout: 20_000 });
    const services = page.getByRole("table", { name: "Services and dependencies" });
    await expect(services.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    for (const [name, state] of [
      ["Database (PostgreSQL)", "Healthy"],
      ["Identity provider (Keycloak)", "Healthy"],
      ["Event broker (Kafka)", "Healthy"],
      ["Device broker (MQTT)", "Healthy"],
      ["Operator API", "Healthy"],
      ["Command executor", "Healthy"],
    ] as const) {
      const row = services.locator("tbody tr").filter({ hasText: name });
      await expect(row, `${name} row`).toContainText(state);
    }
    for (const worker of ["Demo feeder", "Command executor", "Outcome verifier"]) {
      const row = services.locator("tbody tr").filter({ hasText: worker });
      await expect(row).toContainText(/heartbeat/);
      await expect(row).not.toContainText("never reported");
    }
    await expect(services.locator("tbody tr").filter({ hasText: "Database" })).toContainText(/\d+ ms/);
    const sources = page.getByRole("table", { name: "Data freshness by source" });
    await expect(sources.locator("tbody tr").filter({ hasText: "Corridor KPIs" })).toContainText(/Fresh|Stale|Unknown/);
    await expect(sources.locator("tbody tr").filter({ hasText: "Forecasts" })).toContainText(/Fresh|Stale|Unknown/);
    await expect(sources.locator("tbody tr").filter({ hasText: /loop|detector|counter/i }).first()).toContainText(/Fresh|Stale|Unknown/);
    const ingestion = page.locator("#ops-ingestion");
    await expect(ingestion).toContainText("Events stored in the last 5 minutes");
    await expect(ingestion).toContainText("Median time from observation to storage");
    const events = await ingestion.locator("dt", { hasText: "Events stored" }).locator("xpath=following-sibling::dd").innerText();
    expect(Number(events.replace(/,/g, "")), "the feeder is running, so events have arrived").toBeGreaterThan(0);
    await expect(page.getByRole("status").filter({ hasText: /healthy|Needs attention|Nothing could be confirmed/ }).first()).toBeVisible();
    await expectNoAxeViolations(page, "platform status");
  });

  test("'Check now' checks again, updates can be paused, and the page says when it was last checked", async ({ page }) => {
    await signIn(page, userFor("supervisor"));
    await page.goto("/operations");
    const checked = page.getByText(/^Checked \d{2}:\d{2}:\d{2} UTC\./);
    await expect(checked).toBeVisible({ timeout: 20_000 });
    const before = await checked.innerText();
    await page.waitForTimeout(1100);
    await page.getByRole("button", { name: "Check now" }).click();
    await expect.poll(async () => checked.innerText(), { timeout: 15_000 }).not.toBe(before);
    await page.getByRole("button", { name: "Pause updates" }).click();
    await expect(page.locator("[data-feed-state='paused']")).toBeVisible();
    await expect(page.getByText(/updates are paused/)).toBeVisible();
    await page.getByRole("button", { name: "Resume updates" }).click();
    await expect(page.locator("[data-feed-state='connected']")).toBeVisible();
  });

  test("an unreachable API is said plainly, and the last status stays readable with its check time", async ({ page }) => {
    await signIn(page, userFor("supervisor"));
    await page.goto("/operations");
    const services = page.getByRole("table", { name: "Services and dependencies" });
    await expect(services.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await page.route("**/api/v1/ops/status", (route) => route.abort("connectionrefused"));
    await page.getByRole("button", { name: "Check now" }).click();
    await expect(page.getByText(/The status could not be refreshed\. What is shown was checked \d{2}:\d{2}:\d{2} UTC\./)).toBeVisible({ timeout: 15_000 });
    await expect(services.locator("tbody tr").first()).toBeVisible();
    await page.unroute("**/api/v1/ops/status");
    await page.getByRole("button", { name: "Check now" }).click();
    await expect(page.getByText(/The status could not be refreshed/)).toHaveCount(0, { timeout: 15_000 });
  });

  test("operators, supervisors, commanders and auditors may read it; a dispatcher is told which capability it lacks", async ({ browser }) => {
    for (const role of ["operator", "incident_commander", "auditor"]) {
      const page = await asPerson(browser, role, "/operations");
      await expect(page.getByRole("heading", { level: 1, name: "Platform status" }), role).toBeVisible({ timeout: 20_000 });
      await page.context().close();
    }
    const dispatcher = await asPerson(browser, "dispatcher", "/operations");
    await expect(dispatcher.getByRole("heading", { level: 1, name: "Not permitted" })).toBeVisible({ timeout: 20_000 });
    await expect(dispatcher.getByText("ops.view")).toBeVisible();
    await dispatcher.context().close();
  });
});

test.describe("shift handover (P08.09)", () => {
  test("a supervisor writes one with open items, the author cannot acknowledge it, a different person does by name, and a late second acknowledgement is a conflict that names who was first", async ({ page, browser }) => {
    const incident = fixture<{ incident_id: string; element: string }>("incident", "--severity", "high");
    const summary = `Corridor A is slow after the stalled vehicle ${Date.now()}`;
    await signIn(page, userFor("supervisor"));
    await page.goto("/handover");
    await expect(page.getByRole("heading", { level: 1, name: "Shift handover" })).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "Write a handover" }).click();
    const dialog = page.getByRole("dialog", { name: "Write a shift handover" });
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    await dialog.getByRole("button", { name: "Record handover" }).click();
    await expect(dialog.getByText("Write what the next shift needs to know.")).toBeVisible();
    await expect(dialog.getByRole("alert")).toHaveCount(0);
    await dialog.getByLabel("Incoming shift").selectOption("Day");
    await dialog.getByLabel(/Summary/).fill(summary);
    await dialog.getByRole("button", { name: "Record handover" }).click();
    await expect(dialog.getByText("A handover goes from one shift to a different one.")).toBeVisible();
    await dialog.getByLabel("Incoming shift").selectOption("Night");
    await dialog.getByLabel(new RegExp(`Incident: congestion on ${incident.element}`)).check({ timeout: 20_000 });
    await dialog.getByLabel("Anything else").fill("Camera 4 is being replaced at 22:00");
    await expect(dialog).toContainText("Open items");
    await expect(dialog.locator(".kv-row").filter({ hasText: "Open items" })).toContainText("2");
    await expectNoAxeViolations(page, "write handover dialog");
    await dialog.getByRole("button", { name: "Record handover" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Handover recorded", { timeout: 20_000 });
    await dialog.getByRole("button", { name: "Close" }).click();

    const list = page.getByRole("list", { name: "Handovers" });
    const mine = list.locator("li").filter({ hasText: summary });
    await expect(mine).toBeVisible({ timeout: 20_000 });
    await expect(mine).toContainText("Day shift to Night shift");
    await expect(mine).toContainText("Awaiting acknowledgement");
    await expect(mine).toContainText("Written by sam.okafor (Supervisor)");
    await expect(mine.getByRole("link", { name: new RegExp(`congestion on ${incident.element}`) })).toHaveAttribute("href", `/incidents/${incident.incident_id}`);
    await expect(mine).toContainText("Camera 4 is being replaced at 22:00");
    await expect(mine).toContainText("You wrote this handover, so someone else has to acknowledge it.");
    await expect(mine.getByRole("button", { name: "Acknowledge" })).toHaveCount(0);

    const second = await asPerson(browser, "supervisor", "/handover", 1);
    const late = await asPerson(browser, "operator", "/handover", 0);
    const secondCard = second.getByRole("list", { name: "Handovers" }).locator("li").filter({ hasText: summary });
    const lateCard = late.getByRole("list", { name: "Handovers" }).locator("li").filter({ hasText: summary });
    await expect(secondCard).toBeVisible({ timeout: 20_000 });
    await expect(lateCard).toBeVisible({ timeout: 20_000 });

    const ack = secondCard.getByRole("button", { name: "Acknowledge" });
    await ack.click();
    const secondDialog = second.getByRole("dialog", { name: "Acknowledge this handover" });
    await expect(secondDialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    await expect(secondDialog).toContainText("written by sam.okafor");
    await expect(secondDialog).toContainText(`${nameOf(userFor("supervisor", 1))} (${userFor("supervisor", 1)})`);
    await expect(secondDialog).toContainText("cannot be undone");
    await second.keyboard.press("Escape");
    await expect(secondDialog).toBeHidden();
    await expect(ack).toBeFocused();

    await late.getByRole("list", { name: "Handovers" }).locator("li").filter({ hasText: summary }).getByRole("button", { name: "Acknowledge" }).click();
    const lateDialog = late.getByRole("dialog", { name: "Acknowledge this handover" });
    await expect(lateDialog).toBeVisible();

    await ack.click();
    await secondDialog.getByRole("button", { name: "Acknowledge", exact: true }).click();
    await expect(secondDialog.getByRole("alert")).toContainText("Recorded under sam.two", { timeout: 20_000 });
    await secondDialog.getByRole("button", { name: "Close" }).click();
    await expect(secondCard).toContainText("Acknowledged by sam.two");
    await expect(secondCard.getByRole("button", { name: "Acknowledge" })).toHaveCount(0);

    await lateDialog.getByRole("button", { name: "Acknowledge", exact: true }).click();
    await expect(lateDialog.getByRole("alert")).toContainText("sam.two acknowledged it first. Nothing was changed.", { timeout: 20_000 });
    await lateDialog.getByRole("button", { name: "Close" }).click();
    await expect(lateCard).toContainText("Acknowledged by sam.two", { timeout: 20_000 });

    await page.getByLabel("Show").selectOption("awaiting_acknowledgement");
    await expect(page.getByRole("list", { name: "Handovers" }).locator("li").filter({ hasText: summary })).toHaveCount(0, { timeout: 20_000 });
    await page.getByLabel("Show").selectOption("acknowledged");
    await expect(page.getByRole("list", { name: "Handovers" }).locator("li").filter({ hasText: summary })).toHaveCount(1, { timeout: 20_000 });
    await expectNoAxeViolations(page, "handover list");
    await second.context().close();
    await late.context().close();
  });

  test("a role that may only read handovers sees them and no control that writes or acknowledges", async ({ browser }) => {
    for (const role of ["field_responder", "auditor"]) {
      const page = await asPerson(browser, role, "/handover");
      await expect(page.getByRole("heading", { level: 1, name: "Shift handover" }), role).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText("Your role can read handovers. Writing and acknowledging them belongs to the operating roles.")).toBeVisible();
      await expect(page.getByRole("button", { name: "Write a handover" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Acknowledge" })).toHaveCount(0);
      await page.context().close();
    }
  });

  test("the demo operator does not hold handover.view", async ({ browser }) => {
    const demo = await asPerson(browser, "demo_operator", "/handover");
    await expect(demo.getByRole("heading", { level: 1, name: "Not permitted" })).toBeVisible({ timeout: 20_000 });
    await expect(demo.getByText("handover.view")).toBeVisible();
    await demo.context().close();
  });
});

test.describe("demo controls (P08.09)", () => {
  test.beforeEach(() => {
    fixture("demo-clear");
  });

  test("its own identity and banner; start, replay and reset a run, each one confirmed, and the demo trail records them by name", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith("/api/") || path.startsWith("/scenario-control/")) requests.push(`${request.method()} ${path}`);
    });
    await signIn(page, userFor("demo_operator"));
    await page.goto("/demo");
    await expect(page.getByRole("heading", { level: 1, name: "Demo controls" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("DEMO IDENTITY")).toBeVisible();
    await expect(page.getByText("Demonstration controls.")).toBeVisible();
    await expect(page.getByText("They replay recorded events; they do not change the live map or any incident, command or outcome.")).toBeVisible();
    await expect(page.getByText(/^\d of 2 runs in progress\./)).toBeVisible();

    await page.getByRole("button", { name: "Start a scenario run" }).click();
    const dialog = page.getByRole("dialog", { name: "Start a scenario run" });
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    await expect(dialog).toContainText("What it does not do");
    await expect(dialog).toContainText("does not change the live map, any incident, command, call or outcome");
    await expect(dialog).toContainText("recorded in the demo audit trail under your name, not in the operator audit trail");
    await expectNoAxeViolations(page, "start run dialog");
    await dialog.getByRole("button", { name: "Start run" }).click();
    await expect(dialog.getByRole("alert")).toContainText(/Run completed\. [\d,]+ events accepted\. Fingerprint [0-9a-f]{12}\./, { timeout: 30_000 });
    await dialog.getByRole("button", { name: "Close" }).click();

    const runs = page.getByRole("table", { name: "Scenario runs" });
    const row = runs.locator("tbody tr").first();
    await expect(row).toContainText("Completed", { timeout: 20_000 });
    await expect(row).toContainText("dee.moreno");
    const fingerprint = (await row.locator("code").innerText()).trim();
    expect(fingerprint).toMatch(/^[0-9a-f]{12}$/);
    await expect(page.locator("#demo-run-detail")).toContainText("Events accepted");
    await expect(page.locator("#demo-run-detail")).toContainText(fingerprint);

    await row.getByRole("button", { name: "Replay" }).click();
    const replay = page.getByRole("dialog", { name: "Replay this run" });
    await expect(replay.getByRole("button", { name: "Cancel" })).toBeFocused();
    await replay.getByRole("button", { name: "Replay run" }).click();
    await expect(replay.getByRole("alert")).toContainText(`Fingerprint ${fingerprint}`, { timeout: 30_000 });
    await replay.getByRole("button", { name: "Close" }).click();

    await runs.locator("tbody tr").first().getByRole("button", { name: "Reset" }).click();
    const reset = page.getByRole("dialog", { name: "Reset this run" });
    await reset.getByRole("button", { name: "Reset run" }).click();
    await expect(reset.getByRole("alert")).toContainText("Run reset", { timeout: 20_000 });
    await reset.getByRole("button", { name: "Close" }).click();
    await expect(runs.locator("tbody tr").first()).toContainText("Reset", { timeout: 20_000 });
    await expect(runs.locator("tbody tr").first().getByRole("button", { name: "Reset" })).toBeDisabled();

    const trail = page.getByRole("table", { name: "Demo audit trail" });
    for (const action of ["Start", "Replay", "Reset"]) {
      await expect(trail.locator("tbody tr").filter({ hasText: action }).filter({ hasText: "dee.moreno" }).filter({ hasText: "Allowed" }).first()).toBeVisible({ timeout: 20_000 });
    }
    expect(requests.filter((r) => r.includes("/api/")).every((r) => r.endsWith("/api/v1/me")), `only the identity call reaches the operator API: ${requests.join(", ")}`).toBe(true);
    expect(requests.some((r) => r.startsWith("POST /scenario-control/v1/runs")), "the actions went to the scenario-control service").toBe(true);
    await expectNoAxeViolations(page, "demo controls");
  });

  test("when the bound is reached the service refuses a new run and says why, and the refusal is in the demo trail", async ({ page }) => {
    fixture("demo-running", "--count", "2");
    try {
      await signIn(page, userFor("demo_operator"));
      await page.goto("/demo");
      await expect(page.getByText("2 of 2 runs in progress.")).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText("the service will refuse a new run until one finishes")).toBeVisible();
      await page.getByRole("button", { name: "Start a scenario run" }).click();
      const dialog = page.getByRole("dialog", { name: "Start a scenario run" });
      await dialog.getByRole("button", { name: "Start run" }).click();
      await expect(dialog.getByRole("alert")).toContainText("Bound reached. At most 2 scenario runs can be in progress at once", { timeout: 20_000 });
      await dialog.getByRole("button", { name: "Close" }).click();
      const trail = page.getByRole("table", { name: "Demo audit trail" });
      await expect(trail.locator("tbody tr").filter({ hasText: "Denied bound" }).first()).toBeVisible({ timeout: 20_000 });
      await expectNoAxeViolations(page, "demo controls with the bound reached");
    } finally {
      fixture("demo-clear");
    }
  });

  test("the two identities do not cross: operating roles cannot open the demo controls, and the demo operator cannot open operations", async ({ browser }) => {
    const supervisor = await asPerson(browser, "supervisor", "/demo");
    await expect(supervisor.getByRole("heading", { level: 1, name: "Not permitted" })).toBeVisible({ timeout: 20_000 });
    await expect(supervisor.getByText("demo.control")).toBeVisible();
    await expect(supervisor.getByRole("link", { name: "Demo controls" })).toHaveCount(0);
    await supervisor.context().close();
    const demo = await asPerson(browser, "demo_operator", "/actions/commands");
    await expect(demo.getByRole("heading", { level: 1, name: "Not permitted" })).toBeVisible({ timeout: 20_000 });
    for (const path of ["/incidents", "/audit", "/operations", "/dispatch"]) {
      await demo.goto(path);
      await expect(demo.getByRole("heading", { level: 1, name: "Not permitted" }), path).toBeVisible({ timeout: 20_000 });
    }
    await demo.context().close();
  });
});
