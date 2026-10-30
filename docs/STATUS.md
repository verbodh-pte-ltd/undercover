# Status

The claims pilot (`docs/PLAN.md`) and the agent's harness (`docs/PLAN_HARNESS.md`) are built. In the
harness's live run, capped at five claims on DeepSeek V4.1 Flash, the agent planned and worked five
claims on its own: four decided and paid, one approved at $3,750 and waiting for sign-off. The other 20
claims stayed waiting.

- `backend/`: FastAPI with SQLAlchemy Core. With `DATABASE_URL` set (as `npm run dev` does from `.env`)
  it uses local Postgres in Docker, in the `undercover` schema, with a pool of 4 connections. Without it,
  it uses SQLite in `backend/undercover.db` (or the file in `UNDERCOVER_DB`), as the tests do.
  `backend/store.py` seeds the database from `data/` on first start: the 25 claims, the policies and
  the customers. `npm run seed` empties it, the agent's saved threads included, and seeds it again.
  - Read routes for claims, policies, claim history, the policy wording, the handlers, and the policy
    wording and the claims manual by numbered section (`/api/documents/{document}/sections`).
  - Write routes: start a claim inside a run, record steps, record a decision, pay, sign off, and
    release a claim that failed.
  - Runs: `POST /api/runs` starts a run with a cap, `GET /api/runs/current` returns the latest run with
    its to-do list and summary. The backend refuses a claim beyond the cap, and refuses to finish a run
    while a claim is still waiting within the cap.
  - A claim that fails before its decision goes back to `waiting`; after two failures it is
    `needs_handler`. A claim stopped `in_progress` stays there for the agent to carry on, and
    `GET /api/claims/unfinished` lists those claims.
  - `POST /api/payouts` (`work_out_payout`) applies the payout rules in `backend/payout.py` to a
    policy, a benefit and the claim's lines, and says whether the payout needs sign-off.
  - A payout over $1,000 (`payout_approval_limit` in `data/company.json`) is refused until a handler
    signs off. A sign-off records who and when, puts the claim back `in_progress`, and asks the agent's
    service (`UNDERCOVER_AGENT`, default `http://127.0.0.1:8002`) to carry on and pay it.
  - The same decision or payment sent again counts as done and returns the claim; a different decision
    is still refused. A payment records when it was made.
- `backend/instructions/`: `queue.md` for running the queue and `claim.md` for working a claim, served
  by `GET /api/instructions`.
- `agent/`: the UnderCover Agent.
  - The queue agent has four tools (`list_waiting_claims`, `set_todos`, `work_claim`, `finish_run`).
  - `work_claim` starts the claim and runs a fresh claim agent on it. On any error it releases the
    claim and replies in one line, so one bad claim never stops the run.
  - The claim agent has eight tools (`get_claim`, `get_policy`, `get_claim_history`, `list_sections`,
    `read_section`, `calculate_payout`, `record_decision`, `pay_claim`) and posts each step to its claim. It has no sign-off
    tool.
  - Model calls retry twice with backoff and time out after 60 seconds.
  - Each try at a claim has a thread, such as `CLM-1001-1`, set by `start_claim`. With the Langfuse keys
    set, the claim is traced in Langfuse in a session named after its thread.
- `dashboard/`: the claims desk.
  - The queue (`#/`) has a count per state and the current run: its to-do list with each item's state,
    and the summary when it finishes.
  - Each claim (`#/claims/CLM-1001`) shows its decision, reasons, the agent's steps, the sections it
    read, the policy and earlier claims.
  - The instructions page (`#/instructions`) shows both sets of instructions, read only.
  - Light and dark mode. A claim's page checks for new steps every two seconds until the claim is
    decided, and the queue refreshes every five seconds. A claim waiting for sign-off has a handler
    picker and a button that signs off and pays.
- `e2e/`: Playwright tests, one at a time, on their own servers (backend :8001, dashboard :5174) and
  their own database, `e2e/e2e.db`, which is deleted at the start of each run.

## Run it

```bash
npm run dev      # backend on :8000, dashboard on :5173
```

With the backend running, start a run. With no cap it works every waiting claim:

```bash
npm run agent -- --cap 5   # a trial on five claims
npm run agent              # every waiting claim
uv run -m agent CLM-1001   # named claims only, without the queue agent
```

The model is `deepseek/deepseek-v4.1-flash` with low reasoning effort, or `MODEL_NAME` from `.env` (such
as `openrouter/free`).

To reset the data, run `npm run seed`. The `undercover-postgres` container must be running; the start
cell in `labs/week2/week2day3.ipynb` makes it again from `.env` if Docker has lost it.

## Check it

```bash
npm run check    # Python checks, TypeScript checks, then the end-to-end tests
```

## Not built yet

- Scoring the agent against `data/evals/`. The answer key stays unopened until that build.
- Claim agents in parallel, and starting a run from the dashboard.
- Sign-in, real payments and messages to customers, as `docs/SPEC.md` says.
