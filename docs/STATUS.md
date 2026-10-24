# Status

The harness build (`docs/PLAN_HARNESS.md`) is under way. Phases 1 to 4 are done. The agent is now a
queue agent that plans a run as a to-do list and hands each claim to a fresh claim agent through
`work_claim`, on DeepSeek V4.1 Flash by default. Run it with `npm run agent -- --cap 5`, or name claims:
`uv run -m agent CLM-1001`. The backend has runs with a
cap, a to-do list per run, failed claims going back in the queue (`needs_handler` after two failures),
and a check for missed claims before a run finishes. The agent reads the policy wording and the claims
manual by numbered section (`/api/documents/...`), and its instructions are in `backend/instructions/`
(`queue.md` and `claim.md`). After this change, delete `backend/undercover.db` once, because the claims
table has a new column.

The claims pilot, built from `docs/PLAN.md`. Phases 1 to 5 are done. In phase 6 the whole-queue run stopped after 10 of the 25 claims, when
the OpenRouter account ran out of credits; the other 15 claims are still waiting.

- `backend/`: FastAPI on SQLite. `backend/store.py` creates `backend/undercover.db` (or the file in
  `UNDERCOVER_DB`) and seeds it from `data/` on first start: the 25 claims, the policies and the
  customers. Read routes for claims, policies, claim history, the policy wording and the handlers,
  and the write routes: start a claim, record steps, record a decision, pay and sign off. A payout
  over $1,000 (`payout_approval_limit` in `data/company.json`) is refused until a handler signs off,
  and sign-off pays it.
- `backend/instructions.md`: the agent's instructions, served by `GET /api/instructions`.
- `agent/`: the UnderCover Agent. Six tools (`get_claim`, `get_policy`, `get_claim_history`,
  `get_policy_wording`, `record_decision`, `pay_claim`) that call the backend over HTTP. `run()`
  starts the claim and posts each step to the backend. It has no sign-off tool.
- `dashboard/`: the claims desk. The queue (`#/`) with a count per state, each claim in full
  (`#/claims/CLM-1001`) with its decision, reasons, the agent's steps, the policy and earlier claims,
  and the agent's instructions read only (`#/instructions`). Light and dark mode. A claim's page
  checks for new steps every two seconds until the claim is decided, and the queue refreshes every five
  seconds. A claim waiting for sign-off has a handler picker and a button that signs off and pays.
- `e2e/`: Playwright tests on their own servers (backend :8001, dashboard :5174) and their own
  database, `e2e/e2e.db`, which is deleted at the start of each run.

## Run it

```bash
npm run dev      # backend on :8000, dashboard on :5173
```

With the backend running, work the queue (every waiting claim) or chosen claims:

```bash
npm run agent              # or: uv run -m agent CLM-1001 CLM-1002
```

The model is `openai/gpt-6-luna`, or `MODEL_NAME` from `.env` (such as `openrouter/free`).

To reset the data, stop the backend and delete `backend/undercover.db`.

## Check it

```bash
npm run check    # Python checks, TypeScript checks, then the end-to-end tests
```

## Not built yet

- Scoring the agent against `data/evals/`. The answer key stays unopened until that build.
- The claims manual, sign-in, real payments and messages to customers, as `docs/SPEC.md` says.
