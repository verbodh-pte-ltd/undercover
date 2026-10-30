# Status

The claims pilot (`docs/PLAN.md`), the agent's harness (`docs/PLAN_HARNESS.md`), durable state
(`docs/PLAN_DURABLE.md`, from `docs/DURABLE_LOCAL.md`) and the swarm and scoreboard
(`docs/PLAN_SWARM.md`, from `docs/SWARM.md`) are built. We use Postgres in Docker on this machine, not
Supabase.

In the swarm build's live runs on local Postgres with GPT-6 Luna, after one reseed:

- A dispatch at N=3, started on the dashboard, was killed partway by stopping everything on port 8002,
  with 3 claims in progress. After `npm run dev` started again, the service marked it stopped.
- A dispatch at N=25 carried on those 3 claims as the same attempts on the same threads, each with one
  decision, and worked the 13 waiting claims at once (their first saved steps fell within 0.1 seconds).
  The run took 35 seconds; its claims' working times add up to 364 seconds.
- A claim worked from the start took 24.3 seconds on average at N=25, against 16.5 at N=3. The time
  goes in the backend: with 25 agents, each of a claim's 30 to 40 backend calls waits for one of the
  backend's 4 database connections (0.27 seconds a call at 25 at once, against 0.04 at 3).
- A sign-off on the dashboard was paid within five seconds. No claim failed for want of a connection.
  With the pool fix, Postgres showed at most 10 connections during the evaluation at N=25. (Before the
  fix, 25 workers made 25 pools at once and it reached 26.)
- An evaluation of the evaluation set at N=25: 80% right, 92% straight through, $2,175 cost of
  mistakes, $0.0012 of model calls a claim, 43 seconds for the run. One of the held-out set's 5 tiny
  claims at N=5: 100% right, 80% straight through, 23 seconds. The queue's counts were the same before
  and after.
- Through Langfuse's API, the 16 claims of the N=25 dispatch are found together by its tag, `run-2`.

## The parts

- `backend/`: FastAPI with SQLAlchemy Core. With `DATABASE_URL` set (as `npm run dev` does from `.env`)
  it uses local Postgres in Docker, in the `undercover` schema, with a pool of 4 connections that
  callers wait for. Without it, it uses SQLite in `backend/undercover.db` (or the file in
  `UNDERCOVER_DB`), as the tests do. `backend/store.py` seeds the database from `data/` on first start:
  the 25 claims, the policies, the customers, the held-out claims, and the handlers' decisions on both
  labelled sets in `answer_key`, which no claim operation reads. `npm run seed` rebuilds every table,
  empties the agent's saved threads and seeds again, so it empties the scoreboard too.
  - Read routes for claims, policies, claim history, the policy wording, the handlers, and the policy
    wording and the claims manual by numbered section (`/api/documents/{document}/sections`).
  - Write routes: start a claim in a run or none, record steps, record a decision, pay, sign off,
    release a claim that failed, and record a model call's tokens and cost or a stretch of work.
  - Runs are dispatches or evaluations: `create_run`, `list_run_claims` (claims in progress first, then
    waiting claims oldest submitted first, or an evaluation's copies), `finish_run`,
    `stop_running_runs` and `get_current_run` (the latest dispatch, with its claims, summary, model cost
    and time). Only one runs at a time.
  - `start_dispatch` and `start_evaluation` ask the agent's service through `backend/agent_service.py`,
    and pass on its refusal (409) or say it isn't running (503).
  - An evaluation works copies of its set's claims, with ids such as `E3-CLM-1001`, out of the queue.
    Nobody signs off a copy. `list_evaluations` and `get_evaluation` score each one against
    `answer_key`: decision accuracy, accuracy (the payout within a dollar), the cost of mistakes, the
    straight-through rate, model cost and time per claim, and the time for the run.
  - A claim that fails before its decision goes back to `waiting`; after two failures it is
    `needs_handler`. A claim stopped `in_progress` stays there for the next dispatch.
  - `POST /api/payouts` (`work_out_payout`) applies the payout rules in `backend/payout.py`.
  - A payout over $1,000 (`payout_approval_limit` in `data/company.json`) is refused until a handler
    signs off. A sign-off records who and when, puts the claim back `in_progress`, and asks the agent's
    service to carry on and pay it.
  - The same decision or payment sent again counts as done; a different decision is still refused.
- `backend/instructions/claim.md`: the claim agent's instructions, served by `GET /api/instructions`.
- `agent/`: the UnderCover Agent, in five parts (`docs/CODE_STANDARDS.md`).
  - The claim agent is a LangGraph graph (`claim_graph` in `agent/main.py`) with seven tools and no pay
    or sign-off tool, on `openai/gpt-6-luna` with low reasoning effort, or `MODEL_NAME` with none. At
    most 30 model calls a claim. Each model call's tokens and cost are recorded with its run.
  - Progress is saved after every step by `PostgresSaver`, a saver of its own for each claim run on the
    process's shared pool of 4, or in memory without `DATABASE_URL`.
  - `work(claim_id, run_id)` starts a waiting claim or carries on one in progress, tries a failed claim
    once more on a new thread, and lets a rate limit or a timeout through. It is the only place the
    dispatcher touches the claim agent. It records the working time of each stretch of work, so a wait
    for a sign-off is never counted.
  - `agent/dispatcher.py` lists a run's claims and fans out with `Send`, up to N at once. A rate limit or
    timeout waits and carries the claim on, up to 3 tries, then leaves it in progress. It skips a claim
    the service is already working.
  - The agent's service (`agent/service.py`, port 8002) starts dispatches and evaluations (refusing a
    second while one runs), resumes signed-off claims, and marks any run still running as stopped when
    it starts.
  - Each claim is traced in Langfuse in a session named after its thread, tagged `run-<run_id>`.
  - `uv run -m agent CLM-1001` works named claims with no run.
- `dashboard/`: the claims desk.
  - The queue (`#/`) has a count per state, a "Claim agents at once" picker (1 to 25, 3 to start) and a
    "Start dispatch" button, and the latest dispatch: its claims and where each stands, its model cost
    and time, and its summary once it has finished.
  - The Scoreboard (`#/scoreboard`) starts an evaluation (set, whole or tiny, N) and lists evaluations
    newest first with every measure. `#/scoreboard/<run_id>` shows its claims beside the handlers',
    with the misses marked and each claim linked to its page.
  - Each claim (`#/claims/CLM-1001`) shows its decision, reasons, the agent's steps, the sections it
    read, the policy and earlier claims. A claim waiting for sign-off has a handler picker and a "Sign
    off" button, except on an evaluation's copy.
  - The instructions page (`#/instructions`) shows the claim agent's instructions, read only.
- `e2e/`: Playwright tests, one at a time, on their own servers (backend :8001, dashboard :5174) and
  their own SQLite database, `e2e/e2e.db`, deleted at the start of each run. A stand-in for the agent's
  service on :8003 (`e2e/agent-stand-in.mjs`) records the backend's calls with their bodies.

## Run it

```bash
npm run dev      # backend on :8000, dashboard on :5173, agent service on :8002
```

Start a dispatch from the queue page and an evaluation from the Scoreboard page. To work named claims
without a run:

```bash
uv run --env-file .env -m agent CLM-1001
```

The `undercover-postgres` container must be running. To reset the data, run `npm run seed`; the start
cell in `labs/week2/week2day3.ipynb` makes the container again from `.env` if Docker has lost it.

## Check it

```bash
npm run check    # Python checks, TypeScript checks, then the end-to-end tests
```

`npm run check` needs no `.env`: the tests run on SQLite, with no Postgres, Langfuse or OpenRouter.

## Not built yet

- Rejecting a held payout. A handler can only sign off for now.
- Sign-in, real payments and messages to customers, as `docs/SPEC.md` says.
