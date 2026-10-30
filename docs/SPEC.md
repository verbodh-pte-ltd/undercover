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
everything it has worked out. As soon as a handler signs off, it picks the claim up from where it stopped
and pays it; until then the claim shows as waiting for sign-off, and between the sign-off and the
payment as in progress. If the agent isn't running at that moment, it pays the claim the next time it
runs. A handler can only sign off for now.

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
- The agent's instructions, read only.

## Rules

- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording and the claims manual. The wording comes first; the manual says
  how to read it and covers what it leaves open, such as a third claim within 12 months, losses to wild
  animals and meals without receipts.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No sign-in. Everyone who uses the screens sees everything.
- No real payments.
- No messages to customers.