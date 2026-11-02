import { expect, test } from "@playwright/test";
import { AGENT, recordDecision } from "./agent";
import { signIn } from "./sign-in";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("the scoreboard shows an evaluation played out through the API", async ({
  page,
  request,
}) => {
  // The tests run one at a time; finish any run an earlier test left going.
  await request.post("/api/runs/stop-running", { headers: AGENT });
  const created = await request.post("/api/runs", {
    headers: AGENT,
    data: {
      kind: "evaluation",
      n: 5,
      eval_set: "held_out",
      tiny: true,
      model: "stand-in",
      effort: "none",
      version: 1,
    },
  });
  const { run_id } = (await created.json()) as { run_id: number };
  const run = String(run_id);
  const copies = (await (
    await request.get(`/api/runs/${run}/claims`, { headers: AGENT })
  ).json()) as string[];
  expect(copies).toHaveLength(5);

  for (const copy of copies) {
    await request.post(`/api/claims/${copy}/start`, {
      headers: AGENT,
      data: { run_id },
    });
    await recordDecision(request, copy, {
      decision: "refer",
      payout: 0,
      reason: "Needs a handler.",
      policy_section: "Manual 2",
    });
  }
  await request.post(`/api/runs/${run}/finish`, { headers: AGENT });

  await page.goto("/#/scoreboard");
  const row = page.getByRole("row", { name: new RegExp(`Evaluation ${run}`) });
  await expect(row).toBeVisible();
  await expect(row.getByText("Held-out set")).toBeVisible();
  await expect(row.getByText("Tiny")).toBeVisible();
  // e2e/judge-stand-in.mjs grades every claim 2.2, most likely a B.
  await expect(row.getByText("2.20 of 3")).toBeVisible();

  await row.getByRole("link", { name: `Evaluation ${run}` }).click();
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await expect(page.getByText(/stand-in, version 1\./)).toBeVisible();
  // The table names each copy by the claim it copies, such as CLM-1001 for E6-CLM-1001.
  const original = copies[0].slice(copies[0].indexOf("-") + 1);
  const first = page.getByRole("row", { name: new RegExp(original) });
  await expect(first.getByRole("cell", { name: "B, 2.20 of 3" })).toBeVisible();
  await expect(first.getByRole("cell", { name: "Yes" })).toBeVisible();
  await expect(
    first.getByRole("cell", { name: "No", exact: true }),
  ).toBeVisible();
  const link = page.getByRole("link", { name: original, exact: true });
  await expect(link).toHaveAttribute("href", `#/claims/${copies[0]}`);
  await link.click();
  await expect(
    page.getByText(new RegExp(`A copy of .* for evaluation ${run}`)),
  ).toBeVisible();
});
