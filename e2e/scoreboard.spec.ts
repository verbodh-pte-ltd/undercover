import { expect, test } from "@playwright/test";
import { AGENT, asHandler } from "./agent";
import { signIn } from "./sign-in";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("the scoreboard shows an evaluation played out through the API", async ({
  page,
  request,
}) => {
  // The app creates the run and its copies; the agent's stand-in refers each copy and
  // finishes the run, and the judge's stand-in grades it.
  const created = await request.post("/api/evaluations", {
    headers: await asHandler(request),
    data: { n: 5, eval_set: "held_out", tiny: true },
  });
  expect(created.ok()).toBe(true);
  const { run_id } = (await created.json()) as { run_id: number };
  const run = String(run_id);
  await expect
    .poll(
      async () =>
        (
          (await (
            await request.get(`/api/runs/${run}`, { headers: AGENT })
          ).json()) as { status: string }
        ).status,
    )
    .toBe("finished");
  const copies = (
    (await (
      await request.get(`/api/evaluations/${run}`, {
        headers: await asHandler(request),
      })
    ).json()) as { claims: { claim_id: string }[] }
  ).claims.map((c) => c.claim_id);
  expect(copies).toHaveLength(5);

  await page.goto("/#/scoreboard");
  const row = page.getByRole("row", { name: new RegExp(`Evaluation ${run}`) });
  await expect(row).toBeVisible();
  await expect(row.getByText("Held-out set")).toBeVisible();
  await expect(row.getByText("Tiny")).toBeVisible();
  // e2e/judge-stand-in.mjs grades every claim 2.2, most likely a B.
  await expect(row.getByText("2.20 of 3")).toBeVisible();

  await row.getByRole("link", { name: `Evaluation ${run}` }).click();
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await expect(page.getByText(/openai\/gpt-6-luna, version 1\./)).toBeVisible();
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
