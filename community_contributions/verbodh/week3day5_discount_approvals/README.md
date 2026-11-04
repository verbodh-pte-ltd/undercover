# Week 3 Day 5 challenge: a discount agent on AgentCore Runtime

Northwind Software's sales reps ask for discounts on quotes. The discount agent decides each one
against the pricing policy. It approves up to 15% on its own; above that, a sales director signs
off. It is a LangGraph graph, deployed on AgentCore Runtime with boto3, and it has the five parts
the assignment asks for.

| Part | Where |
|---|---|
| Decisions with limits, a person signs off above the limit | `discounts/graph.py`: `check` and `sign_off` |
| Durable state, LangGraph checkpoints in a database | `discounts/app.py`: `PostgresSaver` on Postgres, schema `discounts` |
| An enterprise context layer | `context/` and `discounts/context.py`: the policy, customers, products, people |
| AgentCore Memory with a namespace per user | `discounts/memory.py`: `/reps/{actorId}/preferences/` |
| Deployed on AgentCore Runtime | `deploy.py` and `main.py` |

## The graph

```
recall -> model <-> tools -> check -> (sign_off) -> remember
```

- **recall** reads the rep's notes from their own namespace in AgentCore Memory.
- **model** reads the context layer through three tools, `get_customer`, `get_product` and
  `read_policy`, then calls `SubmitDecision` with approve, counter or decline, a percent and a
  reason.
- **check** is code, not the model. Whatever the model submitted, it declines a customer on hold,
  caps the percent at the product's floor and at what the rep asked, and sends anything over 15%,
  or anything the model couldn't decide in 6 calls, to a director.
- **sign_off** pauses the graph with `interrupt()`. The pause is a checkpoint in Postgres, so the
  director's answer resumes it from another Runtime session. The app checks that the person
  signing is a director before it resumes, so a rep's attempt is refused and the request keeps
  waiting.
- **remember** saves the request and the answer as an event under the rep's id. The Memory's
  user-preference strategy turns them into notes, which `recall` reads on the rep's next request.

## The app

`discounts/app.py` is a `BedrockAgentCoreApp` with one entrypoint and four operations: `request`,
`sign_off`, `status` and `health`. On Runtime its two credentials, the OpenRouter key and the
database URL, come from AgentCore Identity, not from environment variables.

## Running it

From the `undercover` folder, with `OPENROUTER_API_KEY` and `AWS_DATABASE_URL` in `.env`, signed in
to AWS:

```bash
uv run pytest community_contributions/verbodh/week3day5_discount_approvals/tests -q --no-cov
```

Then follow `week3day5_discount_approvals.ipynb`: deploy, five requests, the notes, and teardown.

## What it did on Runtime

Five requests on 4 November 2026, each in a new Runtime session, on `deepseek/deepseek-v4.1-flash`:

1. REP-01 asked 10% for Bluefin Retail (growth tier). **Approved** by the agent, 3 model calls. The
   rep's note asked for "the percent first, then a one-line reason".
2. REP-01 asked 22% for Acme (strategic) on the Security Suite, against a competitor's quote.
   The agent **countered at 20%**, the strategic guide, so it **waited for a director**.
3. REP-02 tried to sign it off: **refused**, still waiting.
4. DIR-01 signed it off from another session: **approved by the director**, resumed from the
   checkpoint on Postgres.
5. Memory had turned REP-01's first request into a note, "Show the percent first, then a one-line
   reason". REP-02's namespace was empty. REP-01's next request, 6% for Cedar, came back as
   "6% — within the standard-tier guide of up to 8%...".

On the laptop, a customer on hold was declined by the code whatever the model said.

One thing I hit: with SigV4 calls and no user id, Runtime gives the agent no workload access
token, and the SDK tries to make a workload identity of its own, which the role can't. Passing
`runtimeUserId`, the person asking, fixes it, and it's the right id to pass anyway.

## What I'd change for production

- The rep and director ids come in the payload. In production they'd come from a signed-in user's
  token, with Runtime's JWT authorizer, as UnderCover's Copilot does.
- The context layer is files in the package. In production it would be the CRM and the price book,
  behind a Gateway.
