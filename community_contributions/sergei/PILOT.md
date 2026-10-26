# Pilot: the UnderCover claims platform

Sergei (Neuromediator), Week 1 Day 3.

- Repo: https://github.com/Neuromediator/undercover/tree/week1day3-sergei
- Plan: [PLAN.md](PLAN.md), a copy of `docs/PLAN.md`

## What I did

1. Changed `docs/BRAND.md` to my own design preferences before planning.
2. Asked Claude Code to plan the pilot from the documents in `docs/`. It wrote `docs/PLAN.md` in about 6 minutes: 6 phases, each with success criteria it could check, and 10 decisions for me to review.
3. Asked it to implement the plan. It took about 28 minutes, with Sonnet at xhigh effort, and used about 10% of my weekly limit on the Pro plan.
4. Every phase was tested before its commit: one commit per phase, none with `--no-verify`. The build followed the documents from the first iteration, with no rework of the plan.

## The 10 decisions it asked me to review

1. The agent runs from the command line, not from the dashboard.
2. A referred claim is `decided` with the outcome `refer`.
3. Sign-off pays the claim, in one operation.
4. The payment must equal the approved payout. Exactly $1,000.00 pays without sign-off, $1,000.01 is held.
5. Phone, email, address and date of birth are never loaded into the backend, because nothing in the pilot uses them.
6. `get_policy` returns the plan's limits, so a small model need not read them from a table.
7. The agent's instructions live in `backend/instructions.md`, served read only.
8. The dashboard polls every 2 seconds, with no websockets.
9. The end-to-end tests use their own ports and database, so they never touch the data I use in development.
10. Files not used: the claims manual, `knowledge.json`, `data/security/` and `data/evals/`.

## What was built

- **Backend:** FastAPI and SQLite, 14 operations. The $1,000 sign-off rule is enforced here, not in the prompt.
- **Agent:** six tools. It reaches the backend only through its API and posts every step as it works.
- **Dashboard:** the queue, the claim page with the agent's steps live, a sign-off form, and the instructions read only. Styled to `BRAND.md` in light and dark.
- **End-to-end:** 6 Playwright tests. A model is never called in tests.

## Checks

- Python: ruff and pyright clean, 61 tests, 99% coverage.
- Dashboard: prettier, eslint and tsc clean, 37 tests, 99% coverage.
- End-to-end: 6 tests, run twice in a row, and the development database stayed untouched.

## A real run

I ran the real model on four claims on both sides of the $1,000 limit and watched each one in the dashboard.

| Claim | Payout | Result |
|---|---|---|
| CLM-1001 | $420 | Paid at once |
| CLM-1002 | $580 | Paid at once ($1,030 eligible, minus the airline's $400 and the $50 deductible) |
| CLM-1008 | $2,240 | Held, then signed off by Dana Whitfield in the dashboard |
| CLM-1003 | $3,750 | Held, then signed off by Priya Raman in the dashboard |

I read the four decisions against the policy wording by hand and they hold. The real run also found two problems the tests had not:

- Under parallel tool calls the backend failed with a SQLite cross-thread error. I wrote a test that failed, then fixed it.
- The model guessed ids, such as `get_policy(policy_id='CLM-1001')`. One line in the instructions fixed it: start with `get_claim`, never guess an id.

## Takeaways

1. **Tests with a stub model do not test the model.** They passed while a real run still found a threading bug. One real run per phase that touches the agent is worth the cost.
2. **Put the rule where the model cannot break it.** The backend refuses to pay over $1,000 until a handler signs off, and a test with a stub model that tries to pay $2,200 shows it holds.
3. **Instructions are code.** One sentence changed how the model behaved. Backend errors say what to do next, because the agent reads them.
4. **A plan with checkable criteria makes a fast build safe to review.** I could tick each phase, and the 10 decisions surfaced choices, such as dropping personal data, before any code existed.

## Not done

I did not score the agent against the handlers' decisions, and `data/evals/` was never opened.
