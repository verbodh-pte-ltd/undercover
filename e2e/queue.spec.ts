import { expect, test } from "@playwright/test";
import { asHandler } from "./agent";
import { signIn } from "./sign-in";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

const standIn = "http://127.0.0.1:8003/calls";

test("the queue lists every claim and opens one", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(26);
  await page.getByRole("link", { name: "CLM-1001" }).click();
  await expect(page.getByText("My flight home from Rome")).toBeVisible();
  await page.getByRole("link", { name: "Back to the queue" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(26);
});

test("the Configuration page shows version 1, read only", async ({ page }) => {
  await page.goto("/#/instructions");
  await expect(
    page.getByRole("heading", { name: "Current version: version 1" }),
  ).toBeVisible();
  await expect(page.getByText(/You decide one travel insurance/)).toBeVisible();
  // Other specs may add versions, so look at version 1's own row.
  await expect(page.getByRole("row", { name: /^1 Current/ })).toContainText(
    "Not scored yet",
  );
  await expect(page.getByRole("textbox")).toHaveCount(0);
});

// The app creates the run, then asks the agent to work it by its id; the stand-in finishes it.
test("starting a dispatch on the dashboard creates its run and reaches the agent", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByLabel("Claim agents at once").selectOption("7");
  await page.getByRole("button", { name: "Start dispatch" }).click();
  const run = { run_id: 0, n: 0 };
  await expect(async () => {
    const calls = (await (await request.get(standIn)).json()) as string[];
    const asked = calls.map((c) => JSON.parse(c) as { run_id?: number });
    Object.assign(
      run,
      await (
        await request.get("/api/runs/current", {
          headers: await asHandler(request),
        })
      ).json(),
    );
    expect(run.n).toBe(7);
    expect(asked).toContainEqual({ operation: "work_run", run_id: run.run_id });
  }).toPass();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText(`Dispatch ${String(run.run_id)}`)).toBeVisible();
  await expect(page.locator(".panel-head .chip")).toHaveText("Finished");
});

test("starting a run while another is going is refused with the reason", async ({
  page,
  request,
}) => {
  // The round holds the agent's work until a handler stops it.
  const started = await request.post("/api/rounds", {
    headers: await asHandler(request),
  });
  const { round_id } = (await started.json()) as { round_id: number };
  await page.goto("/");
  await page.getByRole("button", { name: "Start dispatch" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    `Round ${String(round_id)} is already running`,
  );
  await request.post(`/api/rounds/${String(round_id)}/stop`, {
    headers: await asHandler(request),
  });
});

test("starting an evaluation on the dashboard reaches the agent", async ({
  page,
  request,
}) => {
  await page.goto("/#/scoreboard");
  await page.getByLabel("Set").selectOption("held_out");
  await page.getByLabel("Only its five tiny claims").check();
  await page.getByLabel("Claim agents at once").selectOption("5");
  await page.getByRole("button", { name: "Start evaluation" }).click();
  const evaluation = { run_id: 0, n: 0, eval_set: "" };
  await expect(async () => {
    const calls = (await (await request.get(standIn)).json()) as string[];
    const asked = calls.map((c) => JSON.parse(c) as { run_id?: number });
    const listed = (await (
      await request.get("/api/evaluations", {
        headers: await asHandler(request),
      })
    ).json()) as (typeof evaluation)[];
    Object.assign(evaluation, listed[0]);
    expect([evaluation.n, evaluation.eval_set]).toEqual([5, "held_out"]);
    expect(asked).toContainEqual({
      operation: "work_run",
      run_id: evaluation.run_id,
    });
  }).toPass();
  // The stand-in refers each claim and finishes the run, so the next start goes ahead.
  await expect(
    page.getByRole("row", {
      name: new RegExp(`Evaluation ${String(evaluation.run_id)}`),
    }),
  ).toContainText("Finished");
});
