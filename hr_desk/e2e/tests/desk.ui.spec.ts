import { expect, test, type Page } from "@playwright/test";
import { resetBudgets, resetEmployee } from "../lib/db";
import { PASSWORD, statePath } from "../lib/personas";

async function askInUi(page: Page, question: string) {
  const thread = page.getByRole("list", { name: "Conversation" });
  const answers = thread.locator("li.assistant");
  const before = await answers.count();
  await page.getByLabel("Your question").fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(answers).toHaveCount(before + 1, { timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeEnabled({ timeout: 60_000 });
  return thread;
}

test.describe("account flows", () => {
  test("a listed employee sets a password and lands on Ask HR", async ({ page }) => {
    const email = "e2e.ui-signup@stylo.example";
    await resetEmployee(email, "UI Signup", "officer", false);
    await resetBudgets();
    await page.goto("/sign-up");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("New password").fill(PASSWORD);
    await page.getByRole("button", { name: /set|create|continue|save/i }).first().click();
    await expect(page.getByRole("heading", { name: "Ask about a policy" })).toBeVisible();
  });

  test("an unlisted email cannot create an account", async ({ page }) => {
    await resetBudgets();
    await page.goto("/sign-up");
    await page.getByLabel("Work email").fill("stranger@stylo.example");
    await page.getByLabel("New password").fill(PASSWORD);
    await page.getByRole("button", { name: /set|create|continue|save/i }).first().click();
    await expect(page.getByText(/not on the employee list/i)).toBeVisible();
  });

  test("wrong password shows an error, right password signs in", async ({ page }) => {
    const email = "e2e.ui-signin@stylo.example";
    await resetEmployee(email, "UI Signin", "officer", false);
    await resetBudgets();
    await page.request.post("/api/sign-up", { data: { email, password: PASSWORD } });
    await page.context().clearCookies();
    await page.goto("/sign-in");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill("not-the-password-1");
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByText(/does not match/i)).toBeVisible();
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByRole("heading", { name: "Ask about a policy" })).toBeVisible();
  });
});

test.describe("admin UI gating", () => {
  test.use({ storageState: statePath("officer") });

  test("an officer sees no admin navigation and the People page shows no staff", async ({ page }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Sections" });
    for (const label of ["Policies", "People", "Questions", "Checks"]) {
      await expect(nav.getByRole("link", { name: label })).toHaveCount(0);
    }
    await page.goto("/people");
    await expect(page.getByText("e2e.admin@stylo.example")).toHaveCount(0);
    await expect(page.getByText("insrah.hr@stylo.example")).toHaveCount(0);
  });
});

test.describe("admin UI", () => {
  test.use({ storageState: statePath("admin") });

  test("an admin sees admin navigation and the staff list", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: "People" })).toBeVisible();
    await page.goto("/people");
    await expect(page.getByText("insrah.hr@stylo.example")).toBeVisible();
  });
});

test.describe("asking in the browser", () => {
  test.describe("as an intern", () => {
    test.use({ storageState: statePath("intern") });
    test("travel figures are refused and never rendered", async ({ page }) => {
      await resetBudgets();
      await page.goto("/");
      const thread = await askInUi(page, "What is the daily allowance for a General Manager on tour?");
      await expect(thread).not.toContainText(/\b\d{1,2},?\d{3}\b/);
    });
  });

  test.describe("as a general manager", () => {
    test.use({ storageState: statePath("general_manager") });
    test("own daily allowance is answered and the follow-up keeps context", async ({ page }) => {
      await resetBudgets();
      await page.goto("/");
      const thread = await askInUi(page, "What is my daily allowance on tour?");
      await expect(thread.locator("li.assistant").last()).toContainText(/\d/);
      await askInUi(page, "and what about hotel?");
      await expect(thread.locator("li.assistant").last()).not.toContainText(/couldn't find|which policy/i);
    });

    test("employees never see policy source text or citations", async ({ page }) => {
      await resetBudgets();
      await page.goto("/");
      const thread = await askInUi(page, "What is the Code of Conduct message from the CEO?");
      await expect(thread).not.toContainText(/Source:|Page \d+|passage/i);
    });
  });
});
