import { expect, test } from "@playwright/test";
import { recordDecision } from "./agent";
import { DANA, MARCUS, signIn } from "./sign-in";

test("a handler signs in, sees their name and signs out", async ({ page }) => {
  await signIn(page);
  await expect(page.getByText("Dana Whitfield")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("a wrong password says what to do", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill(DANA);
  await page.getByLabel("Password").fill("wrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "That email and password don't match. Check them and try again.",
  );
});

test("the backend refuses a handler action with no sign-in", async ({
  request,
}) => {
  const response = await request.post("/api/dispatches", { data: { n: 3 } });
  expect(response.status()).toBe(401);
});

test("a handler who can't sign off over the limit is told why", async ({
  page,
  request,
}) => {
  await recordDecision(request, "CLM-1005", {
    decision: "approve",
    payout: 1500,
    reason: "Covered.",
    policy_section: "7. Emergency Medical",
  });
  await signIn(page, MARCUS);
  await page.goto("/#/claims/CLM-1005");
  await expect(page.getByText("Waiting for sign-off")).toBeVisible();
  await expect(
    page.getByText(/You can't sign off payouts over the approval limit/),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /Sign off/ })).toHaveCount(0);
});
