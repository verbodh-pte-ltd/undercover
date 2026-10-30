import { expect, test } from "@playwright/test";

test("the scoreboard shows an evaluation played out through the API", async ({
  page,
  request,
}) => {
  // The tests run one at a time; finish any run an earlier test left going.
  await request.post("/api/runs/stop-running");
  const created = await request.post("/api/runs", {
    data: {
      kind: "evaluation",
      n: 5,
      eval_set: "held_out",
      tiny: true,
      model: "stand-in",
      effort: "none",
    },
  });
  const { run_id } = (await created.json()) as { run_id: number };
  const run = String(run_id);
  const copies = (await (
    await request.get(`/api/runs/${run}/claims`)
  ).json()) as string[];
  expect(copies).toHaveLength(5);

  for (const copy of copies) {
    await request.post(`/api/claims/${copy}/start`, { data: { run_id } });
    await request.post(`/api/claims/${copy}/decision`, {
      data: {
        decision: "refer",
        payout: 0,
        reason: "Needs a handler.",
        policy_section: "Manual 2",
      },
    });
  }
  await request.post(`/api/runs/${run}/finish`);

  await page.goto("/#/scoreboard");
  const row = page.getByRole("row", { name: new RegExp(`Evaluation ${run}`) });
  await expect(row).toBeVisible();
  await expect(row.getByText("Held-out set")).toBeVisible();
  await expect(row.getByText("Tiny")).toBeVisible();

  await row.getByRole("link", { name: `Evaluation ${run}` }).click();
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await page.getByRole("link", { name: copies[0] }).click();
  await expect(
    page.getByText(new RegExp(`A copy of .* for evaluation ${run}`)),
  ).toBeVisible();
});
