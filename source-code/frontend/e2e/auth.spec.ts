import { expect, test } from "@playwright/test";
import { ROLES, endAllSessions, expectNoAxeViolations, expectedNavigation, homeFor, signIn, userFor } from "./support";

test.describe("sign-in, session and access (P08.04)", () => {
  test("an unauthenticated visitor to a protected screen is sent to sign in, and the page never asks for a password itself", async ({ page }) => {
    await page.goto("/incidents");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
    await expect(page.locator("input[type=password]")).toHaveCount(0);
    await expect(page.getByText("SIMULATED", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Skip to main content" })).toHaveCount(1);
    await expectNoAxeViolations(page, "login");
  });

  test("the first Tab stop is the skip link and it moves focus to main", async ({ page }) => {
    await page.goto("/login");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();
  });

  for (const role of ROLES) {
    test(`${role}: lands on its own home screen with exactly the navigation its capabilities allow`, async ({ page }) => {
      const username = userFor(role);
      await signIn(page, username);
      const home = homeFor(role);
      await expect(page).toHaveURL(new RegExp(`${home.path}$`));
      await expect(page.getByRole("heading", { level: 1, name: home.title })).toBeVisible();
      await expect(page.getByTestId("user-role")).toContainText(role.replace(/_/g, " "), { ignoreCase: true });
      const links = await page.getByRole("navigation", { name: "Primary" }).getByRole("link").allInnerTexts();
      expect(links.map((t) => t.trim()).sort()).toEqual(expectedNavigation(role));
      await expectNoAxeViolations(page, `${role} home`);
    });
  }

  test("a deep link is kept across sign-in", async ({ page }) => {
    await page.goto("/actions/commands");
    await expect(page).toHaveURL(/\/login$/);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.locator("#username").fill(userFor("operator"));
    // password is filled by signIn's helper normally; do the same steps here to keep the deep link under test
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const identities = JSON.parse(readFileSync(resolve(process.cwd(), "..", "infra", "platform", "output", "demo_identities.json"), "utf-8"));
    await page.locator("#password").fill(identities[userFor("operator")].password);
    await page.locator("#kc-login").click();
    await expect(page).toHaveURL(/\/actions\/commands$/, { timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible();
  });

  test("a screen the role does not hold the capability for says which capability is missing", async ({ page }) => {
    await signIn(page, userFor("operator"));
    await page.goto("/audit");
    await expect(page).toHaveURL(/\/forbidden\?screen=audit&capability=audit\.view$/);
    await expect(page.getByRole("heading", { level: 1, name: "Not permitted" })).toBeVisible();
    await expect(page.getByText("audit.view")).toBeVisible();
    await expectNoAxeViolations(page, "forbidden");
  });

  test("a field responder is refused the command screens, by capability", async ({ page }) => {
    await signIn(page, userFor("field_responder"));
    await page.goto("/actions/commands");
    await expect(page).toHaveURL(/capability=commands\.view/);
  });

  test("an unknown path shows page-not-found in the shell when signed in, and in the public frame when not", async ({ page }) => {
    await page.goto("/no/such/place");
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" })).toHaveCount(0);
    await signIn(page, userFor("supervisor"));
    await page.goto("/no/such/place");
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  });

  test("a reload keeps the session through the identity provider without asking for the password again", async ({ page }) => {
    await signIn(page, userFor("operator"));
    await page.goto("/incidents");
    await expect(page.getByRole("heading", { level: 1, name: "Incidents" })).toBeVisible({ timeout: 20_000 });
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "Incidents" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("user-role")).toContainText("operator", { ignoreCase: true });
    await expect(page).toHaveURL(/\/incidents$/);
  });

  test("when the identity provider has ended the session, a reload asks for sign-in and the destination survives it", async ({ page }) => {
    const username = userFor("supervisor");
    await signIn(page, username);
    await page.goto("/actions/commands");
    await expect(page.getByRole("heading", { level: 1, name: "Commands and approvals" })).toBeVisible({ timeout: 20_000 });
    await endAllSessions(username);
    await page.reload();
    await expect(page).toHaveURL(/\/login$/, { timeout: 20_000 });
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
    await signIn(page, username, "/login");
    await expect(page).toHaveURL(/\/actions\/commands$/);
  });

  test("signing out ends the session at the identity provider too", async ({ page }) => {
    await signIn(page, userFor("auditor"));
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/, { timeout: 20_000 });
    await expect(page.getByText("You have been signed out.")).toBeVisible();
    await page.goto("/audit");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  });
});
