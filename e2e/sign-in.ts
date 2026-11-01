import { expect, type Page } from "@playwright/test";

export const DANA = "dana.whitfield@undercover.example";
export const MARCUS = "marcus.oyelaran@undercover.example";
// The password e2e/auth-stand-in.mjs accepts for every email.
export const PASSWORD = "e2e-password";

export async function signIn(page: Page, email = DANA) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}
