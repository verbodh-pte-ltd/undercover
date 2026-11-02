import { expect, test } from "@playwright/test";
import { AGENT } from "./agent";
import { signIn } from "./sign-in";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("steps appear on the claim while the agent works", async ({
  page,
  request,
}) => {
  await page.goto("/#/claims/CLM-1002");
  await expect(page.getByText("No steps yet.")).toBeVisible();

  await request.post("/api/claims/CLM-1002/start", {
    headers: AGENT,
    data: { run_id: null },
  });
  const step = (text: string) => ({
    headers: AGENT,
    data: { kind: "tool_call", text },
  });
  await request.post("/api/claims/CLM-1002/steps", step("get_claim(CLM-1002)"));
  await expect(page.getByText("get_claim(CLM-1002)")).toBeVisible();
  await request.post(
    "/api/claims/CLM-1002/steps",
    step("get_policy(POL-2002)"),
  );
  await expect(page.getByText("get_policy(POL-2002)")).toBeVisible();
  await expect(page.getByText("In progress", { exact: true })).toBeVisible();
});

test("a handler's sign-off pays the claim and tells the agent", async ({
  page,
  request,
}) => {
  await request.post("/api/claims/CLM-1003/decision", {
    headers: AGENT,
    data: {
      decision: "approve",
      payout: 1250,
      reason: "The treatment is covered.",
      policy_section: "7. Emergency Medical",
    },
  });
  const blocked = await request.post("/api/claims/CLM-1003/payment", {
    headers: AGENT,
    data: { amount: 1250 },
  });
  expect(blocked.status()).toBe(403);

  await page.goto("/#/claims/CLM-1003");
  await expect(page.getByText("Waiting for sign-off")).toBeVisible();
  await expect(page.getByText("Not paid yet")).toBeVisible();
  await page.getByRole("button", { name: "Sign off $1,250.00" }).click();
  await expect(
    page.getByText(/^Signed off by Dana Whitfield on /),
  ).toBeVisible();
  await expect(page.getByText(/^Paid \$1,250\.00 on /)).toBeVisible();
  await expect(page.getByText("Decided", { exact: true })).toBeVisible();

  // The agent's service is told, so its paused thread finishes without paying.
  await expect(async () => {
    const calls = await request.get("http://127.0.0.1:8003/calls");
    expect(await calls.json()).toContain("POST /claims/CLM-1003/resume");
  }).toPass();
});
