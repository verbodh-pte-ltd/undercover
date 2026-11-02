import { expect, test } from "@playwright/test";
import { AGENT, recordDecision } from "./agent";
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
    data: { kind: "tool_call", text, detail: null },
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
  await recordDecision(request, "CLM-1003", {
    decision: "approve",
    payout: 1250,
    reason: "The treatment is covered.",
    policy_section: "7. Emergency Medical",
  });
  const blocked = await request.post("/api/claims/CLM-1003/payment", {
    headers: AGENT,
    data: { amount: 1250 },
  });
  expect(blocked.status()).toBe(403);

  await request.post("/api/claims/CLM-1003/steps", {
    headers: AGENT,
    data: {
      kind: "lookup",
      text: "read_section: claims_manual section 6, from claims_manual.md",
      detail: {
        tool: "read_section",
        looked_up: "claims_manual section 6",
        files: [
          {
            file: "claims_manual.md",
            system: "Claims operations",
            owner: "Head of claims operations",
            reviewed: "2026-09-01",
          },
        ],
      },
    },
  });

  await page.goto("/#/claims/CLM-1003");
  await expect(page.getByText("Waiting for sign-off")).toBeVisible();
  // The whole record, with its calculation, and where the agent's lookups came from.
  await expect(page.getByRole("cell", { name: "Medical bill" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Payout" })).toBeVisible();
  await expect(
    page.getByText(/^Decided by UnderCover Agent, version 1 on /),
  ).toBeVisible();
  await expect(page.getByText("Claims manual version 2026.2")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Context used" }),
  ).toBeVisible();
  await expect(
    page.getByText("claims_manual.md", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("claims_manual section 6", { exact: true }),
  ).toBeVisible();
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
