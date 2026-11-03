# The claims pilot

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy and our policy wording. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. For each claim it should:

- read the claim, the customer's policy and claim history, and the sections of the policy wording and
  the claims manual that the claim needs
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

Our goal is to handle claims straight through, as accurately as our handlers do.

A handler starts a dispatch from the queue page and chooses how many claim agents work at once, from 1
to 25. A dispatch works every claim left in progress and every waiting claim, oldest submitted first.
Choosing the next claim is a fixed rule, with no model. Only one dispatch or evaluation runs at a time.
A claim that hits a rate limit or a timeout waits and carries on; one that fails with any other error
is tried once more in the same dispatch, and one that fails twice is left for a handler. One bad claim
never stops the dispatch.

When the agent approves a payout that needs a handler's sign-off, it waits for the sign-off and keeps
everything it has worked out; until then the claim shows as waiting for sign-off. The sign-off itself
pays the claim: the backend records the handler, the time and the payment together, and the claim is
decided and paid at once, whether or not the agent is running. The agent then only finishes its own
record of the claim, saves it to memory, and never pays it.

A handler who can sign off over the limit can instead send the decision back to the agent with a note
saying what to look at again, up to 1,000 characters. The claim keeps the decision that was sent back,
with the note, who sent it back and when, and shows as sent back until the agent takes it. The agent
treats the note as a senior handler's review, checked against the policy wording and the claims manual
like everything else, and refers the claim with the reason when the wording doesn't support what the
note asks. Its new decision goes through the same checks as any other: within the limit it is paid,
over it the claim waits for sign-off again, and it can be sent back again. A send back the agent never
received is worked by the next dispatch. Only one dispatch, evaluation or working round runs at a time,
which the app enforces, and a resumed claim runs beside it.

Before the agent's model sees a claim, a second model reads what the customer wrote and says whether
it tries to instruct whoever handles the claim. A claim that does is referred to a handler with a
payout of zero, the check's reason and the section "Security check", and the agent's model never sees
it. The check's result is the first step shown on the claim, it runs once at the start of a claim's
work, its model calls count in the claim's cost, and the improver can't change it. The security team's
CLM-1901, a claim with such an instruction in its story, is seeded at the end of the queue, without its
handler's decision, so it is in neither evaluation set.

## Handlers sign in

Our handlers sign in to the dashboard with their email address and password. There's no sign-up. Every
page needs a signed-in handler, and the header shows who is signed in, with a way to sign out. The
backend checks the handler on every action a handler takes: signing off a payout, starting a dispatch,
an evaluation or a round, stopping a round, approving or rejecting a proposal and changing a setting. A
sign-off records the signed-in handler. Dana Whitfield is our senior handler. Only a handler who can
sign off payouts over the approval limit may start or stop a round, approve or reject the improver's
proposals or change a setting; anyone else sees what they can't do and why, and the backend refuses it.

The agent's side identifies itself to the backend with an API key. Every route only the agent calls,
such as recording a decision, a payment, its steps and its costs, refuses a request without the key.
The reads both use, a claim, a policy, a claim history, the recipes and the agent's own API document,
take the key or a signed-in handler, and everything else the dashboard calls needs a signed-in handler:
the key never stands in for one. Only the health check is open.

## What the agent works from

The agent works from three sources, each with its own owner:

- **The context layer**: data governance's files in `context/`, each naming the system it comes from,
  its owner and when it was last reviewed: the meaning of each field, the terms the wording defines,
  the plans, the claim rules, outside reference data such as storms, strikes, places and product
  prices, and the policy wording and the claims manual by section. The agent only reads it, through
  the AgentCore Gateway when the agent is on the Gateway, or straight from the context layer's own
  service on the laptop.
- **Memory**: the agent's own notes on each customer, in AgentCore Memory. After the agent decides a
  claim it saves the claim (its summary, the benefit, the date and place and the items, never the
  story or the documents) and its decision, and it can search what it remembers about that claim's
  customer, and no other. Nothing is saved for a claim the check referred or for an evaluation's
  copy, and evaluations work without memory. The claim history in our records still counts for
  repeat claims.
- **Our records**, through the app: claims, policies, customers, decisions and payments.

The agent's tools know the claim it works, its policy and its customer, so the model never gives an
id. When the agent is on the Gateway, its tools and the payment go through the Gateway, whose Policy
refuses any payment over $1,000. A payment the Gateway refuses is recorded on the claim, which then
waits for a handler's sign-off, and the sign-off panel says the Gateway refused it.

## A complete decision record

Every decision record holds what claims manual section 14 asks for: the decision, the payout, the
calculation (for an approval, each line of the payout calculation as the payout rules gave it), the
section relied on, the reason, who or what made the decision (the UnderCover Agent with its
configuration version, or the security check) and when, and the claims manual's version in force. The
backend refuses a record that falls short with a reason naming section 14: a blank reason, section or
maker, an approval that pays nothing, a decline or referral that pays something, an approval with no
calculation on file for the claim's current attempt, or a payout that isn't the calculation's. A
handler's sign-off adds who signed off and when.

The claim page shows every step the agent takes as it happens: each tool call, each lookup in the
context layer with the file it came from, each memory search and save, and each refusal, the Gateway's
labelled as its. A Context used panel lists everything looked up, grouped by file with each file's
source.

## The improver

The claim agent's instructions, model and reasoning effort make up one configuration, kept in
versions. Each version records what changed, the reason, who proposed and approved it and when. A
dispatch and an evaluation always use the current version and record it. A judge grades each claim of
an evaluation against the handler's reason and reference, from F to A with a score from 0 to 3, and
checks whether the agent gave the same grounds and the same section.

A second agent, the improver, improves the claim agent in rounds that a handler starts and can stop
on the dashboard, one at a time. A round runs the evaluations the current version still lacks (one on
the evaluation set, two on the held-out set), then the improver follows its recipe: it reads the
results, proposes a new version with its reason, and waits until a handler approves or rejects it. A
rejection's reason goes back to the improver, which may propose again. An approved version is
evaluated once on the evaluation set and twice on the held-out set, and kept or rolled back by our
rule: kept when it meets the objectives, at least one measure improves by more than its noise and none
gets worse by more than its tolerance. A version with no grade is rolled back. Last, the improver
proposes a new recipe, which a handler approves or rejects in the same way.

The improver can't change the business objectives, the noise and tolerance, the spending limit, the
models allowed, the approval limit, the evaluation sets or the judge, and nothing it proposes takes
effect until a handler approves it. Each round has a spending limit that covers the improver's own
model calls, all of its evaluations and the judge.

## Settings

The platform's settings are on the dashboard: the approval limit, the business objectives, the noise
and the tolerance, the spending limit for each round and how many claim agents work at once in the
improver's evaluations. Every handler can see them, and only a handler who can sign off over the
limit can change them. Each change records who made it and when. A new approval limit applies to the
decisions recorded after it. A payment always reads today's limit, so if the limit is lowered between an
approval and its payment, the payment is refused and the claim stays unpaid. The models the claim agent may use are set in `data/models.json` and
can't be changed on the page.

If the agent is stopped partway through a claim, it carries on with that claim from where it got to the
next time it runs. A claim that fails with an error goes back in the queue and starts afresh. No claim
is ever decided or paid twice.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off or decided.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step.
- The current dispatch: each of its claims and where it stands, and when it finishes, a summary built
  from our records: how many were approved, declined and referred, each payout waiting for a sign-off,
  and each claim left for a handler or still in progress, with the model cost and the time.
- The model cost and the agent's working time on each claim, leaving out any wait for a sign-off.
- A scoreboard of evaluations: the agent working copies of our labelled claims, scored against our
  handlers' decisions, with each evaluation's claims beside the handlers' decisions.
- The claim agent's configuration: the current version, every version with its held-out scores, and
  what any version changed from the one before.
- The average grade on the scoreboard, and each claim's grade with its two checks on an evaluation's
  page, with the judge's cost apart from the claim agent's.
- An improvement round as it happens: the first evaluations, the improver working, a change waiting for
  approval with its diff, the evaluations, the verdict, a recipe waiting for approval, and finished or
  stopped. The rounds so far, with the held-out scores before and after, the verdict and why, who
  approved or rejected what and what each round cost, a chart of each version's held-out accuracy and
  grade, and the recipe's versions.
- The settings, with who last changed each.

## Rules

- A payout over the approval limit, a setting that starts at $1,000, needs a handler's sign-off. The agent can approve such a claim, but only the handler's sign-off pays it. The payment the agent calls refuses any amount over the limit, on any claim, so it can never pay such a claim itself.
- Decisions follow the policy wording and the claims manual. The wording comes first; the manual says
  how to read it and covers what it leaves open, such as a third claim within 12 months, losses to wild
  animals and meals without receipts.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The claim agent must never see them. The improver reads our handlers' decisions and reasons on the evaluation set, and nothing in the agent reads the held-out set's: the improver sees only its scores. The dashboard shows both sets' decisions beside the agent's.
- Amounts are in US dollars.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers and the handlers. The plans, the policy wording and the claims manual come from data governance's `context/`, which seeding loads into the app. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No real payments.
- No messages to customers.