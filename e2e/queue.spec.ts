import { expect, test } from "@playwright/test";

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

test("the instructions are shown read only", async ({ page }) => {
  await page.goto("/#/instructions");
  await expect(page.getByText(/running the claims queue/)).toBeVisible();
  await expect(page.getByText(/You decide one travel insurance/)).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});

test("the queue shows the run's to-do list as it changes", async ({
  page,
  request,
}) => {
  const run = await request.post("/api/runs", { data: { cap: 2 } });
  const { run_id } = (await run.json()) as { run_id: number };
  await page.goto("/");
  await expect(
    page.getByText("The agent has not planned this run yet."),
  ).toBeVisible();

  const todos = (state: string) => [
    { claim_id: "CLM-1004", text: "Decide the delayed bag", state },
  ];
  await request.put(`/api/runs/${String(run_id)}/todos`, {
    data: todos("to_do"),
  });
  // The queue page polls every five seconds.
  const nextPoll = { timeout: 10_000 };
  await expect(page.getByText("Decide the delayed bag")).toBeVisible(nextPoll);
  await expect(page.getByText("To do", { exact: true })).toBeVisible();
  await request.put(`/api/runs/${String(run_id)}/todos`, {
    data: todos("done"),
  });
  await expect(page.getByText("Done", { exact: true })).toBeVisible(nextPoll);
});
