# Week 2 Day 4 Lab: Agent Swarms

Notebook: `labs/week2/week2day4.ipynb`

The lab teaches you to work a batch in parallel with a swarm of agents, run by a dispatcher with no LLM, and to see what really limits the speed.

## What it does

- **Builds a dispatcher with no model.** It's a LangGraph graph. `list_claims` picks the claims, and `fan_out` sends one `Send` per claim to a `work_claim` node. `operator.add` merges the results that come back in the same step.
- **Tests it with a stand-in first.** A one-second stand-in replaces the claim agent, so nothing is spent on a model while the graph is built. A timeline chart shows how the claims overlap.
- **Caps the swarm.** `max_concurrency` limits how many claims run at once. It tries 1, 5 and 25.
- **Retries failures.** A `RetryPolicy` reruns a node that fails, such as on a 429 "Too Many Requests", waiting longer each time. It's safe because a recorded decision can't be changed, so a retried claim can't decide twice.
- **Runs real claim agents.** It runs Day 1's agent with the payout tool on the 25 harder claims, 5 at a time and then 25 at a time, with each run in one Langfuse trace.
- **Scores each run.** It uses Day 1's measures plus model cost, total time, slowest claim and seconds per claim, and sends the scores to the Langfuse trace with `create_score`.

## What the scoreboard shows

- The swarm saves time, and the run takes about as long as its slowest claim.
- Cost per claim stays about the same, and accuracy comes from the agent, not from how many run at once.
- The limits are outside the agent: the model provider's rate limits, and the database's connections (15 on Supabase's free session pooler).

## Why it matters for UnderCover

- It previews the Week 2 Day 4 build: a dispatcher with a swarm of claim agents.
- In the product, each claim gets its own thread, because a claim over the limit can wait days. On one shared thread, a single `interrupt()` would pause the whole dispatch.

## The assignment

- Add a `score_run` node after `work_claim` that scores the whole run once every claim agent has finished. Try it with the stand-in first.
- Stretch: compile the dispatcher with Day 3's `PostgresSaver`, restart the kernel mid-run, and resume. See which claims LangGraph works again.
