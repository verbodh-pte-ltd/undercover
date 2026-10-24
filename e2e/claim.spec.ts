import { expect, test } from "@playwright/test";

test("steps appear on the claim while the agent works", async ({
  page,
  request,
}) => {
  await page.goto("/#/claims/CLM-1002");
  await expect(page.getByText("No steps yet.")).toBeVisible();

  const run = await request.post("/api/runs", { data: { cap: 1 } });
  const { run_id } = (await run.json()) as { run_id: number };
  await request.post("/api/claims/CLM-1002/start", { data: { run_id } });
  const step = (text: string) => ({ data: { kind: "tool_call", text } });
  await request.post("/api/claims/CLM-1002/steps", step("get_claim(CLM-1002)"));
  await expect(page.getByText("get_claim(CLM-1002)")).toBeVisible();
  await request.post(
    "/api/claims/CLM-1002/steps",
    step("get_policy(POL-2002)"),
  );
  await expect(page.getByText("get_policy(POL-2002)")).toBeVisible();
  await expect(page.getByText("In progress", { exact: true })).toBeVisible();
});

test("a handler signs off a payout over $1,000", async ({ page, request }) => {
  await request.post("/api/claims/CLM-1003/decision", {
    data: {
      decision: "approve",
      payout: 1250,
      reason: "The treatment is covered.",
      policy_section: "7. Emergency Medical",
    },
  });
  const blocked = await request.post("/api/claims/CLM-1003/payment", {
    data: { amount: 1250 },
  });
  expect(blocked.status()).toBe(403);

  await page.goto("/#/claims/CLM-1003");
  await expect(page.getByText("Waiting for sign-off")).toBeVisible();
  await expect(page.getByText("Not paid yet")).toBeVisible();
  await page.getByLabel("Handler").selectOption({ label: "Dana Whitfield" });
  await page
    .getByRole("button", { name: "Sign off and pay $1,250.00" })
    .click();
  await expect(page.getByText("Paid $1,250.00")).toBeVisible();
  await expect(page.getByText("Decided", { exact: true })).toBeVisible();
});
