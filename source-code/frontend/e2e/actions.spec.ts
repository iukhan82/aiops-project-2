import { expect, test, type Browser, type Page } from "@playwright/test";
import { expectNoAxeViolations, fixture, signIn, userFor } from "./support";

async function commands(page: Page, role: string): Promise<void> {
  await signIn(page, userFor(role));
  await page.goto("/actions/commands");
  await expect(page.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("table", { name: "Commands" }).locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
}

/** A person asks for a sign message through the real screen; returns the new command's id. */
async function requestSign(page: Page, message: string): Promise<string> {
  await page.getByRole("button", { name: "New command" }).click();
  const dialog = page.getByRole("dialog", { name: "Request a command" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await dialog.getByLabel("Message").fill(message);
  await dialog.getByRole("button", { name: "Request command" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Command requested", { timeout: 20_000 });
  const href = await dialog.getByRole("link", { name: "Open the command" }).getAttribute("href");
  await dialog.getByRole("button", { name: "Close" }).click();
  return href!.split("/").pop()!;
}

async function asPerson(browser: Browser, role: string, path: string): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, userFor(role));
  await page.goto(path);
  return page;
}

test.describe("recommendations, commands and outcomes (P08.08)", () => {
  test("the list shows every lifecycle state as its own shape and word, waiting commands first", async ({ browser, page }) => {
    fixture("policy-outage");
    const operator = await asPerson(browser, "operator", "/actions/commands");
    await expect(operator.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
    await requestSign(operator, `waiting ${Date.now()}`);
    await operator.context().close();
    await commands(page, "supervisor");
    const table = page.getByRole("table", { name: "Commands" });
    const first = table.locator("tbody tr").first();
    await expect(first).toContainText(/Pending approval|Policy unavailable/);
    const states = await table.locator("[data-status]").evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute("data-status")))]);
    for (const wanted of ["requested", "denied", "executed", "failed", "expired", "requested_policy_unavailable"]) expect(states, `a ${wanted} command exists`).toContain(wanted);
    const shapes = await page.evaluate(() => {
      const seen = new Map<string, string>();
      document.querySelectorAll("[data-status]").forEach((el) => {
        const key = el.getAttribute("data-status")!;
        const label = el.textContent?.trim() ?? "";
        const svg = el.querySelector("svg")?.innerHTML ?? "";
        seen.set(key, `${label}|${svg}`);
      });
      return [...seen.values()];
    });
    expect(new Set(shapes).size, "every state has its own label and glyph").toBe(shapes.length);
    await page.getByLabel("State").selectOption("denied");
    await expect(page.getByText(/^\d+ commands? shown\.$/)).toBeVisible();
    await expect(table.locator("tbody tr").first()).toContainText("Denied");
  });

  test("a person who requested a command sees they cannot decide it, and no Review control", async ({ page }) => {
    await commands(page, "operator");
    const message = `mine ${Date.now()}`;
    const id = await requestSign(page, message);
    await page.goto(`/actions/commands/${id}`);
    await expect(page.getByText("You requested this command, so someone else has to approve or deny it.")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Review" })).toHaveCount(0);
    await expect(page.getByRole("list", { name: "Command lifecycle" })).toContainText("Pending approval");
  });

  test("approval is a confirmation that restates everything, defaults to Cancel, shows the four-eyes and policy facts, and the executor - not a person - executes", async ({ browser }) => {
    const operator = await asPerson(browser, "operator", "/actions/commands");
    await expect(operator.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
    const id = await requestSign(operator, `Lane closed ahead ${Date.now()}`);

    const supervisor = await asPerson(browser, "supervisor", `/actions/commands/${id}`);
    await expect(supervisor.getByRole("heading", { level: 1, name: "Command detail" })).toBeVisible({ timeout: 20_000 });
    const opener = supervisor.getByRole("button", { name: "Review" });
    await opener.click();
    const dialog = supervisor.getByRole("dialog", { name: /Review: Show a message on a sign/ });
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    for (const label of ["Action", "Safety class", "Target", "Requested by", "Reason", "Expected benefit", "Expected harm", "Constraints", "Expires"]) await expect(dialog).toContainText(label);
    await expect(dialog).toContainText("SC-0");
    await expect(dialog).toContainText("alex.chen (Operator)");
    await expect(dialog).toContainText("you are a different person, so you may decide");
    await expect(dialog).toContainText("Approving does not execute anything");
    await supervisor.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();

    await opener.click();
    await dialog.getByRole("button", { name: "Approve" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Approved. The policy checked it again against what is true now and it passed. The command executor will run it; nothing has been executed yet.", { timeout: 30_000 });
    await expect(dialog.getByRole("button", { name: "Approve" })).toHaveCount(0);
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(supervisor.locator("#command-details")).toContainText("sam.okafor (Supervisor)");

    await expect(supervisor.locator("#command-lifecycle")).toContainText("Executed", { timeout: 120_000 });
    await expect(supervisor.locator("#command-history")).toContainText("system:command-executor");
    await expect(supervisor.locator("#command-details")).toContainText("Adapter acknowledged");
    await expect(supervisor.locator("#command-outcome")).toContainText("waiting for the outcome verifier");
    await operator.context().close();
    await supervisor.context().close();
  });

  test("denying needs a reason, records who denied and why, and the requester sees it", async ({ browser }) => {
    const operator = await asPerson(browser, "operator", "/actions/commands");
    await expect(operator.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
    const id = await requestSign(operator, `Do not show ${Date.now()}`);
    const supervisor = await asPerson(browser, "supervisor", `/actions/commands/${id}`);
    await supervisor.getByRole("button", { name: "Review" }).click();
    const dialog = supervisor.getByRole("dialog", { name: /Review:/ });
    await dialog.getByRole("button", { name: "Deny" }).click();
    await expect(dialog.getByText("Say why you are denying it.")).toBeVisible();
    await dialog.getByLabel("Reason (required to deny)").fill("The sign is not needed while the lane is open");
    await dialog.getByRole("button", { name: "Deny" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Denied. denied by reviewer: The sign is not needed while the lane is open", { timeout: 20_000 });
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(supervisor.locator("#command-lifecycle")).toContainText("Denied");
    await expect(supervisor.getByRole("alert").filter({ hasText: "will not succeed if retried" })).toBeVisible();
    await expect(supervisor.locator("#command-history")).toContainText("sam.okafor");
    await operator.goto(`/actions/commands/${id}`);
    await expect(operator.getByRole("alert").filter({ hasText: "The sign is not needed while the lane is open" })).toBeVisible({ timeout: 20_000 });
    await operator.context().close();
    await supervisor.context().close();
  });

  test("a role that cannot review is shown no Review button anywhere; an auditor can read but not act", async ({ page }) => {
    await commands(page, "auditor");
    await expect(page.getByRole("button", { name: "Review" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "New command" })).toHaveCount(0);
    await page.getByLabel("State").selectOption("requested");
    await page.getByRole("table", { name: "Commands" }).locator("tbody a").first().click();
    await expect(page.getByRole("heading", { level: 1, name: "Command detail" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Review" })).toHaveCount(0);
  });

  test("failed, expired and policy-unavailable commands each say what happened and whether retrying can help", async ({ page }) => {
    fixture("policy-outage");
    await commands(page, "supervisor");
    for (const [state, text] of [
      ["failed", /invalid_target/],
      ["requested_policy_unavailable", /policy check could not run/],
      ["expired", /Expired/],
    ] as const) {
      await page.goto("/actions/commands");
      await page.getByLabel("State").selectOption(state);
      await page.getByRole("table", { name: "Commands" }).locator("tbody a").first().click();
      await expect(page.getByRole("heading", { level: 1, name: "Command detail" })).toBeVisible({ timeout: 20_000 });
      await expect(page.getByRole("list", { name: "Command lifecycle" })).toBeVisible();
      await expect(page.locator("main")).toContainText(text);
    }
  });

  test("recommendations show benefit, harm, confidence and the safety bounds, never present as executed, and 'take no action' cannot be requested", async ({ page }) => {
    await signIn(page, userFor("operator"));
    await page.goto("/actions/recommendations");
    await expect(page.getByRole("heading", { level: 1, name: "Recommendations" })).toBeVisible({ timeout: 20_000 });
    const rec = page.locator(".rec").first();
    await expect(rec).toBeVisible({ timeout: 20_000 });
    await expect(rec).toContainText("Expected benefit");
    await expect(rec).toContainText("Expected harm");
    await expect(rec).toContainText("Predicted");
    await expect(rec).toContainText("Checked against safety bounds");
    await expect(rec).toContainText(/Proposed|Command requested/);
    await expect(rec.getByRole("list", { name: "Alternatives" }).locator("li").filter({ hasText: "Take no action" }).getByRole("button")).toHaveCount(0);
    await expectNoAxeViolations(page, "recommendations");
  });

  test("requesting from a recommendation is a confirmation, needs no double submit, and leaves the command waiting for someone else", async ({ page }) => {
    await signIn(page, userFor("operator"));
    await page.goto("/actions/recommendations");
    const button = page.getByRole("button", { name: "Request this command" }).first();
    await expect(button).toBeVisible({ timeout: 20_000 });
    await button.click();
    const dialog = page.getByRole("dialog", { name: "Request this command" });
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    for (const label of ["Action", "Safety class", "Target", "Reason", "Expected benefit", "Expected harm", "Constraints", "Expires"]) await expect(dialog).toContainText(label);
    await expect(dialog).toContainText("Requesting is not executing");
    await dialog.getByRole("button", { name: "Request command" }).dblclick();
    await expect(dialog.getByRole("alert")).toContainText(/Command requested|Already requested/, { timeout: 20_000 });
    await expect(dialog.getByRole("button", { name: "Request command" })).toHaveCount(0);
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("link", { name: "open it" })).toBeVisible();
  });

  test("a role that may read recommendations but not request has no request control, and says why", async ({ page }) => {
    await signIn(page, userFor("auditor"));
    await page.goto("/actions/recommendations");
    await expect(page.getByRole("heading", { level: 1, name: "Recommendations" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: /Request this command/ })).toHaveCount(0);
    await expect(page.getByText("Requesting a command needs the commands.request capability.")).toBeVisible();
  });

  test("outcomes name all four classes, and each selected outcome shows what was measured, the thresholds, and any physical undo", async ({ page }) => {
    await signIn(page, userFor("auditor"));
    await page.goto("/actions/outcomes");
    await expect(page.getByRole("heading", { level: 1, name: "Verified outcomes" })).toBeVisible({ timeout: 20_000 });
    const table = page.getByRole("table", { name: "Verified outcomes" });
    await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    for (const [cls, label] of [
      ["effective", "Effective"],
      ["ineffective", "Ineffective"],
      ["unsafe", "Unsafe (rolled back)"],
      ["unknown", "Unknown (escalated)"],
    ] as const) {
      await page.getByLabel("Outcome", { exact: true }).selectOption(cls);
      await expect(table.locator("tbody tr").first()).toContainText(label, { timeout: 20_000 });
    }
    await page.getByLabel("Outcome", { exact: true }).selectOption("unsafe");
    await table.locator("tbody tr").first().getByRole("button").click();
    const detail = page.locator("#outcome-detail");
    await expect(detail).toContainText("Physical undo", { timeout: 20_000 });
    await expect(detail).toContainText(/reopened lanes/i);
    await expect(detail).toContainText("Rolled back");
    await expect(detail).toContainText("Not executed in this session");
    await expect(detail).toContainText("Thresholds");
    await page.getByLabel("Outcome", { exact: true }).selectOption("effective");
    await table.locator("tbody tr").filter({ hasText: "109.0" }).getByRole("button").click();
    await expect(detail).toContainText("109.0 s");
    await expect(detail).toContainText("79.0 s");
    const measuredHere = table.locator("tbody tr").filter({ hasNotText: "109.0" });
    if ((await measuredHere.count()) > 0) {
      await measuredHere.first().getByRole("button").click();
      await expect(detail).toContainText("does not react to commands");
    }
    await page.getByLabel("Outcome", { exact: true }).selectOption("unknown");
    await table.locator("tbody tr").first().getByRole("button").click();
    await expect(detail).toContainText("Escalated because");
    await expectNoAxeViolations(page, "outcomes");
  });

  test("no accessibility violations on the command list, the detail and the review dialog", async ({ browser, page }) => {
    await commands(page, "supervisor");
    await expectNoAxeViolations(page, "commands list");
    const operator = await asPerson(browser, "operator", "/actions/commands");
    await expect(operator.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
    const id = await requestSign(operator, `axe ${Date.now()}`);
    await operator.context().close();
    await page.goto(`/actions/commands/${id}`);
    await expect(page.getByRole("heading", { level: 1, name: "Command detail" })).toBeVisible({ timeout: 20_000 });
    await expectNoAxeViolations(page, "command detail");
    await page.getByRole("button", { name: "Review" }).click();
    await expect(page.getByRole("dialog", { name: /Review:/ })).toBeVisible();
    await expectNoAxeViolations(page, "review dialog");
  });
});
