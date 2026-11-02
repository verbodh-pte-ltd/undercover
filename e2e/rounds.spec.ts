import { expect, test } from "@playwright/test";
import { AGENT, asHandler } from "./agent";
import { signIn } from "./sign-in";

test.beforeEach(async ({ page }) => {
  await signIn(page);
  await page.goto("/#/improvement");
});

// The agent's stand-in accepts the start and the approval; the test records what the
// agent's service would, through the backend's own routes.
test("a handler approves a proposal played out through the API", async ({
  page,
  request,
}) => {
  await page.getByRole("button", { name: "Start a round" }).click();
  await page
    .getByRole("dialog", { name: "Start a round?" })
    .getByRole("button", { name: "Start a round" })
    .click();
  const [round] = (await (
    await request.get("/api/rounds", { headers: await asHandler(request) })
  ).json()) as {
    round_id: number;
  }[];
  const id = String(round.round_id);
  await expect(
    page.getByRole("heading", { name: `Round ${id}` }),
  ).toBeVisible();
  await expect(page.getByRole("list", { name: /stages/ })).toContainText(
    "First evaluations",
  );

  const current = (await (
    await request.get("/api/configuration/current", { headers: AGENT })
  ).json()) as { instructions: string };
  await request.post(`/api/rounds/${id}/stage`, {
    headers: AGENT,
    data: { stage: "improving" },
  });
  await request.post(`/api/rounds/${id}/proposed-change`, {
    headers: AGENT,
    data: {
      instructions: `${current.instructions}\nDecline a claim with no receipt.`,
      model: "openai/gpt-6-luna",
      effort: "medium",
      reason: "Most misses were approvals with no receipt",
    },
  });

  await expect(
    page.getByText("A change to the claim agent waits for approval"),
  ).toBeVisible();
  await expect(page.getByText("low to medium")).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Changed lines" }).locator(".added").last(),
  ).toContainText("Decline a claim with no receipt.");
  await page.getByRole("button", { name: "Approve" }).click();
  await page
    .getByRole("dialog", { name: "Approve this change?" })
    .getByRole("button", { name: "Approve" })
    .click();

  // The backend passes the approval and the signed-in handler to the agent's service.
  await expect
    .poll(async () => {
      const calls = (await (
        await request.get("http://127.0.0.1:8003/calls")
      ).json()) as string[];
      const sent = calls.find((c) =>
        c.startsWith(`POST /rounds/${id}/decision `),
      );
      return (
        sent && (JSON.parse(sent.split(" ").slice(2).join(" ")) as unknown)
      );
    })
    .toEqual({ decision: "approve", reason: "", handler_id: "HND-01" });
  await request.post(`/api/rounds/${id}/decision`, {
    headers: AGENT,
    data: { decision: "approve", handler_id: "HND-01", reason: "" },
  });
  await expect(
    page.getByText(/Dana Whitfield approved the change/),
  ).toBeVisible();
  await expect(page.getByRole("cell", { name: /Version 2/ })).toBeVisible();

  // Stopped before its verdict, the round rolls version 2 back.
  await page.getByRole("button", { name: "Stop the round" }).click();
  await page
    .getByRole("dialog", { name: `Stop round ${id}?` })
    .getByRole("button", { name: "Stop the round" })
    .click();
  await expect(page.getByText(/Stopped by Dana Whitfield/)).toBeVisible();
  await page.getByRole("link", { name: "Configuration" }).click();
  await expect(page.getByRole("row", { name: /^2 Rolled back/ })).toBeVisible();
});
