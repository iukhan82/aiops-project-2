import { expect, test, type Page } from "@playwright/test";
import { expectNoAxeViolations, fixture, signIn, userFor } from "./support";

const newIncident = (severity = "high") => fixture<{ incident_id: string; element: string }>("incident", "--severity", severity);

async function openIncident(page: Page, id: string, role = "operator"): Promise<void> {
  await signIn(page, userFor(role));
  await page.goto(`/incidents/${id}`);
  await expect(page.getByRole("heading", { level: 1, name: "Incident investigation" })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#incident-summary")).toBeVisible({ timeout: 20_000 });
}

test.describe("incident queue and investigation (P08.07)", () => {
  test("the queue is critical first, filterable, and every row links to the incident", async ({ page }) => {
    newIncident("critical");
    newIncident("low");
    await signIn(page, userFor("operator"));
    await page.goto("/incidents");
    const table = page.getByRole("table", { name: "Incident queue" });
    await expect(table).toBeVisible({ timeout: 20_000 });
    const severities = await table.locator("tbody tr").evaluateAll((rows) => rows.map((r) => r.querySelector("[data-status]")?.getAttribute("data-status") ?? ""));
    const rank = ["critical", "high", "medium", "low"];
    const ranks = severities.map((s) => rank.indexOf(s));
    expect(ranks, "rows are in severity order").toEqual([...ranks].sort((a, b) => a - b));
    await expect(table.locator("tbody tr").first().getByRole("link")).toHaveAttribute("href", /\/incidents\/[0-9a-f-]{36}/);
    await page.getByLabel("Status").selectOption("resolved");
    await expect(page.getByText(/^\d+ incidents? shown\.$/)).toBeVisible();
    await expect(page.getByText(/incidents loaded/)).toBeVisible();
    await page.getByLabel("Status").selectOption("all");
    await page.getByLabel("Type").selectOption({ index: 1 });
    await expect(table).toBeVisible();
  });

  test("the queue shows severity and status as shape and word, owner and confidence with its inferred label", async ({ page }) => {
    newIncident("critical");
    await signIn(page, userFor("supervisor"));
    await page.goto("/incidents");
    const first = page.getByRole("table", { name: "Incident queue" }).locator("tbody tr").first();
    await expect(first).toContainText("Critical", { timeout: 20_000 });
    await expect(first).toContainText(/Open|Acknowledged|Investigating|Escalated|Reopened/);
    await expect(first).toContainText(/Traffic operations|Emergency desk|Control desk|Backend on-call|Security|Unassigned/);
    await expect(first).toContainText("Inferred");
    await expectNoAxeViolations(page, "incident queue");
  });

  test("the detail reads as evidence, not verdict: hypotheses are inferred and no cause is verified", async ({ page }) => {
    const { incident_id } = newIncident("high");
    await openIncident(page, incident_id);
    const summary = page.locator("#incident-summary");
    await expect(summary).toContainText("High");
    await expect(summary).toContainText("Open");
    await expect(summary).toContainText("Inferred");
    await expect(summary).toContainText("None. No person has verified a cause.");
    await expect(page.locator("#incident-hypotheses")).toContainText(/None of them is the cause until a person verifies it/);
    await expect(page.locator("#incident-timeline")).toContainText("Created to Open");
    await expect(page.locator("#incident-timeline")).toContainText("e2e-fixture");
    await expect(page.locator("#incident-timeline")).toContainText("Oldest first");
  });

  test("an operator moves the incident through its lifecycle from the stepper, and only legal next steps are controls", async ({ page }) => {
    const { incident_id } = newIncident("high");
    await openIncident(page, incident_id);
    const stepper = page.getByRole("list", { name: "Incident lifecycle" });
    await expect(stepper.getByRole("button", { name: "Record acknowledged" })).toBeVisible();
    await expect(stepper.getByRole("button", { name: "Record open" })).toHaveCount(0);
    await stepper.getByRole("button", { name: "Record acknowledged" }).click();
    await expect(page.getByText("Recorded: the incident is now Acknowledged.")).toBeVisible();
    await expect(page.locator("#incident-summary")).toContainText("Acknowledged");
    await expect(stepper.locator('[aria-current="step"]')).toContainText("Acknowledged");
    await expect(page.locator("#incident-timeline")).toContainText("alex.chen");
    await stepper.getByRole("button", { name: "Record investigating" }).click();
    await expect(page.locator("#incident-summary")).toContainText("Investigating");
  });

  test("notes and ownership are recorded with the person, and appear in the timeline", async ({ page }) => {
    const { incident_id } = newIncident("medium");
    await openIncident(page, incident_id);
    await page.getByRole("button", { name: "Add note" }).click();
    await expect(page.getByText("A note cannot be empty.")).toBeVisible();
    await page.getByLabel("Add a note").fill("Tow truck requested, ETA 15 minutes");
    await page.getByRole("button", { name: "Add note" }).click();
    await expect(page.getByText("Note added to the timeline.")).toBeVisible();
    await expect(page.locator("#incident-timeline")).toContainText("Tow truck requested, ETA 15 minutes");
    await expect(page.locator("#incident-timeline")).toContainText("alex.chen (Operator)");
    await page.getByLabel("Owner", { exact: true }).selectOption("EMERG");
    await page.getByRole("button", { name: "Set owner" }).click();
    await expect(page.getByText("Owner set to Emergency desk.")).toBeVisible();
    await expect(page.locator("#incident-summary")).toContainText("Emergency desk");
    await expect(page.locator("#incident-timeline")).toContainText("Owner changed from OPS to EMERG");
  });

  test("resolving is a confirmation: focus starts on Cancel, Escape returns focus to the opener, a note is required, and the result is shown in place", async ({ page }) => {
    const { incident_id } = newIncident("high");
    await openIncident(page, incident_id);
    const stepper = page.getByRole("list", { name: "Incident lifecycle" });
    const opener = stepper.getByRole("button", { name: "Record resolved" });
    await opener.focus();
    await opener.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Resolve this incident" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    await expect(dialog).toContainText("Will become");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await opener.click();
    await dialog.getByRole("button", { name: "Resolve incident" }).click();
    await expect(dialog.getByText("A note is required to resolve an incident.")).toBeVisible();
    await dialog.getByLabel("Why is it resolved").fill("Blockage cleared by the tow truck");
    await dialog.getByRole("button", { name: "Resolve incident" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Resolved. The incident is resolved and the note is on its timeline.");
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(page.locator("#incident-summary")).toContainText("Resolved");
    await expect(page.locator("#incident-timeline")).toContainText("Blockage cleared by the tow truck");
    await page.getByRole("button", { name: "Reopen incident" }).click();
    await expect(page.locator("#incident-summary")).toContainText("Reopened");
  });

  test("a change made from an out-of-date screen is refused with what the incident is now, and nothing is overwritten", async ({ browser, page }) => {
    const { incident_id } = newIncident("high");
    await openIncident(page, incident_id);
    const other = await browser.newContext();
    const second = await other.newPage();
    await openIncident(second, incident_id, "supervisor");
    await second.getByRole("list", { name: "Incident lifecycle" }).getByRole("button", { name: "Record acknowledged" }).click();
    await expect(second.locator("#incident-summary")).toContainText("Acknowledged");
    await page.getByRole("list", { name: "Incident lifecycle" }).getByRole("button", { name: "Record escalated" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "This incident is now acknowledged, not open" })).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: "Someone else has moved this incident" })).toBeVisible();
    await expect(page.locator("#incident-summary")).toContainText("Acknowledged");
    await other.close();
  });

  test("a role that may read but not manage sees the incident with no action controls, and says why", async ({ page }) => {
    const { incident_id } = newIncident("high");
    await openIncident(page, incident_id, "auditor");
    await expect(page.getByText("Changing it needs the incidents.manage capability.")).toBeVisible();
    await expect(page.getByRole("button", { name: /Record / })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Add note" })).toHaveCount(0);
    await expect(page.locator("#incident-actions")).toHaveCount(0);
  });

  test("an unknown incident says so and links back to the queue", async ({ page }) => {
    await signIn(page, userFor("operator"));
    await page.goto("/incidents/00000000-0000-0000-0000-000000000000");
    await expect(page.getByText("There is no such incident")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("link", { name: "Back to the incident queue" })).toBeVisible();
  });

  test("no accessibility violations on the detail, the resolve dialog and the read-only view", async ({ page }) => {
    const { incident_id } = newIncident("high");
    await openIncident(page, incident_id);
    await expectNoAxeViolations(page, "incident detail");
    await page.getByRole("list", { name: "Incident lifecycle" }).getByRole("button", { name: "Record resolved" }).click();
    await expect(page.getByRole("dialog", { name: "Resolve this incident" })).toBeVisible();
    await expectNoAxeViolations(page, "resolve dialog");
  });
});
