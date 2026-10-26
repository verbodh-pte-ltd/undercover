# Week 2 Day 1 Lab: Evals and Observability

Notebook: `labs/week2/week2day1.ipynb`

The lab teaches you to measure an agent properly, so you can tell whether a change makes it better or worse.

## What it does

- **Traces the agent.** Langfuse records every model call, tool call, token count and timing, so you can see how the agent reached each decision.
- **Scores it against the handlers.** On 25 harder claims, it compares the agent's decisions and payouts with what UnderCover's handlers decided.
- **Uses business-aware scores, not just accuracy.** It uses a confusion matrix, precision and recall, and kappa, which corrects for lucky guessing. It also uses a cost of mistakes, where a wrongful decline costs more than a needless referral.
- **Runs experiments.** It compares the agent working out payouts itself with the agent using a payout tool. This shows that fixed business rules belong in code, not in the model.

## Why it matters for UnderCover

- These evals are how the agent is judged in later builds. Week 2 Day 4 adds evaluations to the product, and Week 2 Day 5's improver agent depends on scores like these.

## What the assignment showed

- One run isn't enough. Two runs of the concise wording against the full wording picked different winners, which is why the lab has `N_TIMES`.
- The write-up is in PR #50: https://github.com/ed-donner/undercover/pull/50
