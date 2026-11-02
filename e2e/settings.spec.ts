import { expect, test } from "@playwright/test";
import { asHandler } from "./agent";
import { signIn } from "./sign-in";

test.beforeEach(async ({ page }) => {
  await signIn(page);
  await page.goto("/#/settings");
});

// The spending limit for each round, so no other spec's payouts change.
test("a handler changes a setting after confirming it", async ({
  page,
  request,
}) => {
  const limit = page.getByLabel("Spending limit for each round");
  await expect(limit).toHaveValue("0.5");
  await limit.fill("0.75");
  await page.getByRole("button", { name: "Save changes" }).click();
  const dialog = page.getByRole("dialog", { name: "Save these changes?" });
  await expect(dialog).toContainText(
    "Spending limit for each round: $0.50 to $0.75",
  );
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("status")).toHaveText(
    "Saved. The new values apply from now on.",
  );
  await expect(page.getByText(/Last changed by Dana Whitfield/)).toBeVisible();
  await page.reload();
  await expect(limit).toHaveValue("0.75");
  const saved = (await (
    await request.get("/api/settings", { headers: await asHandler(request) })
  ).json()) as {
    settings: { round_spending_limit: number };
  };
  expect(saved.settings.round_spending_limit).toBe(0.75);
});

test("the appearance switches at once and survives a reload", async ({
  page,
}) => {
  const html = page.locator("html");
  const body = page.locator("body");
  await page.getByLabel("Dark").check();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(body).toHaveCSS("background-color", "rgb(4, 35, 46)");
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(page.getByLabel("Dark")).toBeChecked();
  await page.getByLabel("Light").check();
  await expect(body).toHaveCSS("background-color", "rgb(240, 244, 245)");
});
