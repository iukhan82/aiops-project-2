import { expect, test } from "@playwright/test";
import { endAllSessions, expectNoAxeViolations, fillLogin, recordMetrics, settled, signIn, userFor } from "./support";

// P08.10 failure workflows: what a person sees when the API, the data or the identity provider fails, on the real stack. The failure is
// produced at the browser's network boundary (a refused connection) or by the identity provider itself (an administrator ending the
// session); the clock is moved forward with Playwright's clock so "the token has expired" needs no five-minute wait.

const results: Record<string, boolean> = {};
test.afterAll(() => recordMetrics("failure_workflows", results));

test.describe("failure workflows (P08.10)", () => {
  test("the API becoming unreachable is said plainly: the rows stay, the header says values may be older, it goes offline after three failures, and it recovers on its own", async ({ page }) => {
    await signIn(page, userFor("supervisor"));
    await page.goto("/actions/commands");
    const table = page.getByRole("table", { name: "Commands" });
    await expect(table.locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    const rows = await table.locator("tbody tr").count();
    await page.route("**/api/v1/**", (route) => route.abort("connectionrefused"));
    await expect(page.getByText("The list could not be refreshed. What is shown was current at the last refresh.")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("[data-feed-state='reconnecting'], [data-feed-state='offline']")).toBeVisible();
    await expect(page.locator(".feed-status")).toContainText("values may be older than shown");
    expect(await table.locator("tbody tr").count(), "the rows already loaded stay").toBe(rows);
    await expect(page.locator("[data-feed-state='offline']")).toBeVisible({ timeout: 45_000 });
    await expectNoAxeViolations(page, "commands with the API unreachable");
    await page.unroute("**/api/v1/**");
    await expect(page.getByText("The list could not be refreshed.")).toHaveCount(0, { timeout: 30_000 });
    await expect(page.locator("[data-feed-state='connected']")).toBeVisible();
    results.api_unreachable_then_recovers = true;
  });

  test("a screen that cannot load at all says what could not be loaded and offers a retry that works", async ({ page }) => {
    await signIn(page, userFor("supervisor"));
    await page.route("**/api/v1/incidents**", (route) => route.abort("connectionrefused"));
    await page.getByRole("link", { name: "Incidents", exact: true }).click();
    const failure = page.getByRole("alert").filter({ hasText: "could not be loaded" });
    await expect(failure).toBeVisible({ timeout: 30_000 });
    await expect(failure).toContainText("The API could not be reached");
    await expect(failure).toContainText("This may be temporary.");
    await expectNoAxeViolations(page, "incidents that could not load");
    await page.unroute("**/api/v1/incidents**");
    await failure.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByRole("table", { name: "Incident queue" }).locator("tbody tr").first()).toBeVisible({ timeout: 30_000 });
    results.screen_that_cannot_load_retries = true;
  });

  test("stale data is never left looking live: thirty minutes without a new reading turns every fresh device stale, on screen", async ({ page }) => {
    await page.clock.install();
    await signIn(page, userFor("supervisor"));
    await page.goto("/analytics/devices");
    await expect(page.getByRole("heading", { level: 1, name: "Device health" })).toBeVisible({ timeout: 30_000 });
    await settled(page);
    const fresh = page.locator('[data-freshness="fresh"]');
    const before = await fresh.count();
    expect(before, "some devices are reporting now").toBeGreaterThan(0);
    await page.route("**/api/v1/**", (route) => route.abort("connectionrefused"));
    await page.clock.fastForward("30:00");
    await expect(fresh, "no badge still says Fresh").toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator('[data-freshness="stale"]').first()).toContainText(/Stale, observed/);
    await expectNoAxeViolations(page, "device health thirty minutes later");
    results.fresh_badges_before = before > 0;
    results.no_fresh_badge_after_thirty_minutes_without_data = true;
  });

  test("a token that cannot be renewed because the session ended elsewhere leads to the session-ended screen, keeps the destination, and signing in again returns to it", async ({ page }) => {
    const user = userFor("supervisor");
    await page.clock.install();
    await signIn(page, user);
    await page.goto("/actions/commands");
    await expect(page.getByRole("table", { name: "Commands" }).locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await endAllSessions(user);
    await page.clock.fastForward("10:00");
    await expect(page).toHaveURL(/\/session-expired/, { timeout: 45_000 });
    await expect(page.getByRole("heading", { level: 1, name: "Session ended" })).toBeVisible();
    await expect(page.getByText("Your session ended, so nothing you see here is live any more.")).toBeVisible();
    await expectNoAxeViolations(page, "session ended");
    await page.getByRole("button", { name: "Sign in again" }).click();
    await fillLogin(page, user);
    await page.waitForURL((url) => url.origin === "http://localhost:5173" && url.pathname === "/actions/commands", { timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
    results.session_ended_mid_use_keeps_destination = true;
  });

  test("an identity provider that cannot be reached when the token needs renewing is told apart from an ended session, and Try again returns to where the person was", async ({ page }) => {
    await page.clock.install();
    await signIn(page, userFor("supervisor"));
    await page.goto("/actions/commands");
    await expect(page.getByRole("table", { name: "Commands" }).locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await page.route("**/realms/aiops/**", (route) => route.abort("connectionrefused"));
    await page.clock.fastForward("10:00");
    await expect(page).toHaveURL(/\/session-expired/, { timeout: 45_000 });
    await expect(page.getByRole("heading", { level: 1, name: "Sign-in service unreachable" })).toBeVisible();
    await expect(page.getByText("The sign-in service cannot be reached, so your session could not be renewed.")).toBeVisible();
    await expect(page.getByText("Your session ended")).toHaveCount(0);
    await expectNoAxeViolations(page, "sign-in service unreachable");
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("alert")).toContainText("The sign-in service still cannot be reached.", { timeout: 15_000 });
    await page.unroute("**/realms/aiops/**");
    await page.getByRole("button", { name: "Try again" }).click();
    await page.waitForURL((url) => url.origin === "http://localhost:5173" && url.pathname === "/actions/commands", { timeout: 45_000 });
    await expect(page.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
    results.identity_provider_unreachable_then_returns_to_destination = true;
  });

  test("an identity provider that is down when a page is opened says so on the sign-in screen, and signing in once it is back lands on the page that was asked for", async ({ page }) => {
    await signIn(page, userFor("supervisor"));
    await page.goto("/actions/commands");
    await expect(page.getByRole("table", { name: "Commands" }).locator("tbody tr").first()).toBeVisible({ timeout: 20_000 });
    await page.route("**/realms/aiops/**", (route) => route.abort("connectionrefused"));
    await page.reload();
    await expect(page).toHaveURL(/\/login/, { timeout: 30_000 });
    await expect(page.getByRole("alert")).toContainText("The identity provider may be unreachable", { timeout: 20_000 });
    await expectNoAxeViolations(page, "sign-in with the identity provider down");
    await page.unroute("**/realms/aiops/**");
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL((url) => url.origin === "http://localhost:5173" && url.pathname === "/actions/commands", { timeout: 45_000 });
    await expect(page.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
    results.identity_provider_down_at_open_then_returns_to_destination = true;
  });
});
