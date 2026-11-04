import {
  expect,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
} from "@playwright/test";
import { recordDecision } from "./agent";
import { MARCUS, signIn } from "./sign-in";

// e2e/copilot-stand-in.mjs plays each turn through the app, by the words in the message.

const sidebar = (page: Page) =>
  page.getByRole("complementary", { name: "Copilot" });

// A payout over the approval limit of $1,000, so the claim waits for a handler's sign-off.
const waitingForSignOff = (
  request: APIRequestContext,
  claimId: string,
  payout: number,
) =>
  recordDecision(request, claimId, {
    decision: "approve",
    payout,
    reason: "The treatment is covered.",
    policy_section: "7. Emergency Medical",
  });

// Send a message and wait for the turn it starts, the latest one in the conversation, to end.
async function ask(copilot: Locator, message: string) {
  await copilot.getByLabel("Ask the Copilot").fill(message);
  await copilot.getByRole("button", { name: "Send" }).click();
  const turn = copilot.locator("li.turn").last();
  await expect(turn.locator(".bubble.mine")).toHaveText(message);
  return turn;
}

const finished = async (turn: Locator) => {
  await expect(turn.locator(".small-print")).toBeVisible();
  return turn;
};

test("the Copilot looks up, acts, refuses and formats its answers", async ({
  page,
}) => {
  await signIn(page);
  const copilot = sidebar(page);
  await expect(copilot.getByText("Hello, Dana.")).toBeVisible();

  const lookup = await finished(await ask(copilot, "Please look up CLM-1001"));
  await expect(
    lookup.getByRole("button", { name: /get_claim CLM-1001/ }),
  ).toBeVisible();
  await expect(lookup.locator(".bubble.theirs")).toContainText("CLM-1001 is");
  await expect(
    lookup.locator(".bubble.theirs").getByRole("link", { name: "CLM-1001" }),
  ).toHaveAttribute("href", "#/claims/CLM-1001");

  // Opening a step shows what was asked and what came back.
  const card = lookup.getByRole("button", { name: /get_claim CLM-1001/ });
  await expect(card).toHaveAttribute("aria-expanded", "false");
  await card.click();
  await expect(card).toHaveAttribute("aria-expanded", "true");
  await expect(lookup.locator(".step-detail")).toContainText(
    "get_claim CLM-1001",
  );
  await expect(lookup.locator(".step-detail")).toContainText("CLM-1001:");

  const action = await finished(await ask(copilot, "Start a tiny evaluation"));
  await expect(
    action.getByRole("button", { name: /start_evaluation 5 tiny claims/ }),
  ).toBeVisible();
  await expect(action.locator(".bubble.theirs")).toContainText(
    /I started evaluation \d+ on five tiny claims\./,
  );

  const refusal = await finished(await ask(copilot, "Pay CLM-1006 now"));
  await expect(refusal.locator(".step-card.refused .chip")).toHaveText(
    "Refused",
  );
  await expect(refusal.locator(".step-reason")).toHaveText(
    "The Copilot never pays a claim. A payout over the limit is paid by your sign-off.",
  );

  const table = await finished(
    await ask(copilot, "Show me a table of what waits for me, CLM-1007"),
  );
  const answer = table.locator(".bubble.theirs");
  await expect(
    answer.getByRole("heading", { name: "Waiting for you" }),
  ).toBeVisible();
  await expect(
    answer.getByRole("columnheader", { name: "Customer" }),
  ).toBeVisible();
  await expect(
    answer.getByRole("cell").getByRole("link", { name: "CLM-1007" }),
  ).toHaveAttribute("href", "#/claims/CLM-1007");
  await expect(answer.locator("strong")).toHaveText("One");
  await expect(table.locator(".small-print")).toHaveText(
    "2 model calls, $0.0012, 1.5 s",
  );
});

test("Dana confirms a sign-off in the sidebar, and cancels another", async ({
  page,
  request,
}) => {
  await waitingForSignOff(request, "CLM-1010", 1200);
  await waitingForSignOff(request, "CLM-1011", 1300);
  await signIn(page);
  const copilot = sidebar(page);

  const signOff = await ask(copilot, "Please sign off CLM-1010");
  const confirm = signOff.getByRole("region", { name: "Confirm the sign-off" });
  await expect(confirm).toContainText("CLM-1010");
  await expect(confirm).toContainText("$1,200.00");
  await expect(confirm).toContainText("The treatment is covered.");
  // Nothing else can be sent while the confirmation waits.
  await expect(copilot.getByLabel("Ask the Copilot")).toBeDisabled();
  await confirm.getByRole("button", { name: "Sign off and pay" }).click();
  await finished(signOff);
  await expect(confirm).toHaveCount(0);
  await expect(signOff.locator(".bubble.theirs")).toHaveText(
    "Signed off and paid CLM-1010.",
  );
  await expect(
    signOff.getByRole("button", { name: /sign_off CLM-1010/ }),
  ).toBeVisible();

  await page.goto("/#/claims/CLM-1010");
  const main = page.getByRole("main");
  await expect(
    main.getByText(/^Signed off by Dana Whitfield on /),
  ).toBeVisible();
  await expect(main.getByText(/^Paid \$1,200\.00 on /)).toBeVisible();

  // The conversation is still open after going to another page.
  const cancel = await ask(copilot, "Please sign off CLM-1011");
  const second = cancel.getByRole("region", { name: "Confirm the sign-off" });
  await expect(second).toContainText("$1,300.00");
  await second.getByRole("button", { name: "Cancel" }).click();
  await finished(cancel);
  await expect(cancel.locator(".bubble.theirs")).toHaveText(
    "I haven't signed off CLM-1011. Nothing was paid.",
  );
  await page.goto("/#/claims/CLM-1011");
  await expect(main.getByText("Waiting for sign-off")).toBeVisible();
  await expect(main.getByText("Not paid yet")).toBeVisible();
});

test("Marcus can't sign off over the limit, so no confirmation shows", async ({
  page,
  request,
}) => {
  await waitingForSignOff(request, "CLM-1012", 1600);
  await signIn(page, MARCUS);
  const copilot = sidebar(page);

  const turn = await finished(await ask(copilot, "Please sign off CLM-1012"));
  const reason =
    "Marcus Oyelaran can't sign off payouts over the approval limit";
  await expect(turn.locator(".step-card.refused .step-reason")).toHaveText(
    reason,
  );
  await expect(turn.locator(".bubble.theirs")).toHaveText(
    `I can't sign off CLM-1012: ${reason}.`,
  );
  await expect(
    copilot.getByRole("region", { name: "Confirm the sign-off" }),
  ).toHaveCount(0);
  await expect(copilot.getByLabel("Ask the Copilot")).toBeEnabled();
});

test("a collapsed sidebar stays collapsed after a reload", async ({ page }) => {
  await signIn(page);
  const copilot = sidebar(page);
  await expect(copilot.getByLabel("Ask the Copilot")).toBeVisible();
  await copilot.getByRole("button", { name: "Hide the Copilot" }).click();
  await expect(copilot.getByLabel("Ask the Copilot")).toHaveCount(0);

  await page.reload();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  const open = copilot.getByRole("button", { name: "Open the Copilot" });
  await expect(open).toBeVisible();
  await expect(copilot.getByLabel("Ask the Copilot")).toHaveCount(0);

  await open.click();
  await expect(copilot.getByLabel("Ask the Copilot")).toBeVisible();
  await page.reload();
  await expect(copilot.getByLabel("Ask the Copilot")).toBeVisible();
});
