import { expect, test } from "@playwright/test";

const standIn = "http://127.0.0.1:8003/calls";

test("the queue lists every claim and opens one", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(25);
  await page.getByRole("link", { name: "CLM-1001" }).click();
  await expect(page.getByText("My flight home from Rome")).toBeVisible();
  await page.getByRole("link", { name: "Back to the queue" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(25);
});

test("the claim agent's instructions are shown read only", async ({ page }) => {
  await page.goto("/#/instructions");
  await expect(page.getByText(/You decide one travel insurance/)).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});

test("starting a dispatch on the dashboard reaches the agent's service with N", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByLabel("Claim agents at once").selectOption("7");
  await page.getByRole("button", { name: "Start dispatch" }).click();
  await expect(async () => {
    const calls = (await (await request.get(standIn)).json()) as string[];
    expect(calls).toContain('POST /dispatches {"n":7}');
  }).toPass();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("starting an evaluation on the dashboard reaches the agent's service", async ({
  page,
  request,
}) => {
  await page.goto("/#/scoreboard");
  await page.getByLabel("Set").selectOption("held_out");
  await page.getByLabel("Only its five tiny claims").check();
  await page.getByLabel("Claim agents at once").selectOption("5");
  await page.getByRole("button", { name: "Start evaluation" }).click();
  await expect(async () => {
    const calls = (await (await request.get(standIn)).json()) as string[];
    expect(calls).toContain(
      'POST /evaluations {"n":5,"eval_set":"held_out","tiny":true}',
    );
  }).toPass();
});
