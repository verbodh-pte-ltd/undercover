# Week 2 Day 5 Lab: Self-Improvement

Notebook: `labs/week2/week2day5.ipynb`

The lab teaches you to let an agent improve another agent safely: with a judge to grade its reasoning, a held-out set to catch overfitting, business limits it can't change, and a person approving each change.

## What it does

- **Uses two sets of claims.** It uses Week 1's queue of 25 as the evaluation set, which the improver learns from. It also uses 25 held-out claims that the improver never reads, to show whether a change works on claims it hasn't seen.
- **Adds a judge with a reference answer.** Jev grades the agent's rationale against the handler's reason, from F to A, with a score from 0 to 3. Two more questions say which part matched. The judge is checked first on four rationales whose grades we already know.
- **Keeps the agent as versioned configuration.** Its instructions, model and reasoning level form a configuration, and every change is a new version. Version 1 is the Day 4 agent.
- **Measures the noise.** Version 1 runs twice on the held-out set to show how much scores move with no change at all. A change has to beat that before it counts.
- **Puts the business rules in code.** The objectives (floors on accuracy and grade), the noise, the tolerance, the budget and the allowed models live in code where the improver can't change them. A version is kept only if it meets the objectives, improves one score by more than the noise, and worsens none by more than the tolerance. Otherwise it's rolled back.
- **Pauses for a human.** The improver follows a recipe: read the results, propose a change, wait for approval, evaluate again. `HumanInTheLoopMiddleware` pauses it on `propose_change` and `propose_recipe` until you approve or reject.
- **Improves the improver.** The recipe's last step proposes a better recipe, and round 2 starts from it. That's recursive self-improvement on a small scale.
- **Sets up the product.** It adds the handlers as users in Supabase Auth with sign-up turned off, adds three lines to `.env`, and sets a credit limit on the OpenRouter key.

## Why it matters for UnderCover

- An improver can keep a deployed agent getting better after the project team has moved on, with the business setting the objectives and a person approving each change.
- It's the basis of the Week 2 Day 5 build: the agent's configuration in versions, an improver agent and sign-in.

## The assignment

- Give the improver another lever: the concise policy wording as a choice in the configuration. See whether it finds a cheaper prompt that still meets the objectives.
