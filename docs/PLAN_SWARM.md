# Plan: a swarm of claim agents and a scoreboard

The brief is `docs/SWARM.md`. A dispatcher with no model replaces the queue agent and works the queue
with up to N claim agents at once. Each claim's cost and time are recorded. Handlers start dispatches
and evaluations from the dashboard, and a Scoreboard page shows how the agent compares with our handlers.

DATABASE NOTE: As in `docs/DURABLE_LOCAL.md`, we use Postgres running locally in Docker, not Supabase.
The database is the `undercover-postgres` container (Postgres 17) on `127.0.0.1:5432`, and `DATABASE_URL`
in `.env` points at it. Wherever the brief says Supabase, read local Postgres. There is no Supabase MCP
server, so connections are counted with `docker exec undercover-postgres psql -U postgres`.

SIDENOTE: For consistency with the course, please read `labs/week2/week2day4.ipynb` and build the
dispatcher in the same style as the lab. Where the lab and this brief differ, follow the brief.

## Choices for you

Review these first. The documents in `docs/` don't make these decisions.

1. **The brief's notes on the earlier build follow Ed's build, not ours.** It names `write_todos`,
   `e2e/run.spec.ts`, four parts of the agent in `docs/CODE_STANDARDS.md`, and rules 10 and 13 there.
   Ours has `set_todos`, `e2e/queue.spec.ts`, and a six-rule `docs/CODE_STANDARDS.md` with no parts. I
   follow the intent: remove our to-do list and its test, and add a short section to
   `docs/CODE_STANDARDS.md` naming the agent's five parts (tools, claim graph, harness, service,
   dispatcher) and the rule that only the harness function touches the claim agent.
2. **A failed claim is tried once more straight away, by the same worker.** The worker carries the claim
   on. If it comes back released to the queue after its first failure, the worker starts it again at
   once on its new thread. A second failure leaves it for a handler. This keeps the dispatcher to the
   lab's two nodes, with no second round of fan-out.
3. **Rate limits are retried by the graph, not inside the model call.** The claim graph drops its
   `with_retry` wrapper, so a `ModelRateLimitError` or `ModelTimeoutError` reaches the worker's
   `RetryPolicy` (3 tries) quickly. `ChatOpenAI` keeps its own 2 quick retries. The harness function
   lets those two errors through without releasing the claim, so each retry carries on from the last
   saved step as the same attempt.
4. **Which run a claim is in travels in the config**, as `configurable.run_id`, not in the claim's
   saved state. A claim started in one dispatch and carried on in the next, or resumed by the service
   after a sign-off (no run), records each model call against the run working it at that moment.
5. **Time is measured around each stretch of work.** The harness times each call that streams the
   graph, and records it when the call ends. A wait for a sign-off falls between two calls, so it is
   never counted.
6. **New tables:** `runs` gains `kind` (dispatch or evaluation), `n`, `eval_set`, `tiny`, `model`,
   `effort`, `status` (running, finished, stopped) and start and finish times, and loses `cap`, `todos`
   and `summary`. `model_calls` holds each call's tokens and cost. `work_times` holds each stretch of
   work. `answer_key` holds the handlers' decisions for both sets, and `held_out_claims` holds the
   held-out claims. Claims gain `run_id` (set only on evaluation copies) and `copied_from`.
7. **An evaluation copy's id** is its run and the original, such as `E7-CLM-1001`, also written into the
   copy's own record. Copies are left out of the queue, the waiting list and the unfinished list.
8. **A copy held for sign-off counts as finished.** The `settle` node sees that the claim is a copy and
   goes to the end without pausing, and the harness deletes the copy's thread from the saver. The
   backend refuses a sign-off on a copy with 409, and the claim page shows no sign-off form for one.
9. **The backend scores evaluations** from its own records and `answer_key`, in one operation,
   `get_evaluation`. Nothing that reads a claim touches `answer_key`.
10. **Model and effort:** `openai/gpt-6-luna` with `reasoning_effort="low"`, or `MODEL_NAME` with no
    effort. The service records `none` for the effort in that case.
11. **Connections:** the service's checkpointer pool is 4, with a 120-second wait for a free connection.
    Each claim run gets its own `PostgresSaver` on that pool. The backend's SQLAlchemy pool stays at 4,
    with its wait raised to 120 seconds, so 25 agents posting steps queue rather than fail. With the
    backend, the service and one `uv run -m agent` that makes at most 12.
12. **The command line keeps one job.** `uv run -m agent CLM-1001` works named claims through the
    harness function, with no run. `uv run -m agent` with no claim id prints how to use it. `npm run
    agent` is removed, since dispatches start from the dashboard.
13. **The Windows kill** for the live check is `Get-NetTCPConnection -LocalPort 8002 -State Listen` then
    `Stop-Process -Force` on each owner and its uvicorn workers, the equivalent of `kill -9 $(lsof -ti
    tcp:8002)`.
14. **Browser checks use the Chrome DevTools MCP server**, because the Playwright MCP server is not
    connected here. The dashboard is opened at `http://[::1]:5173`, because another app on this machine
    holds `127.0.0.1:5173`.

## Approach

### The dispatcher (`agent/dispatcher.py`)

As in the lab: a `list_claims` node and a `work_claim` worker, one `Send` per claim, compiled with no
checkpointer and run with `max_concurrency` N.

- `list_claims` asks the backend for the claims to work: for a dispatch, the claims in progress, then
  the waiting claims, oldest submitted first; for an evaluation, its copies. It leaves out any claim the
  service is already resuming after a sign-off.
- `work_claim` calls `work(claim_id, run_id)` in the harness, the only place the dispatcher touches the
  claim agent. A `ModelRateLimitError` or `ModelTimeoutError` waits and carries the claim on, up to 3
  tries; after that the claim is left in progress with an error step, and the dispatch goes on. (The
  brief names a `RetryPolicy` and an `error_handler` for this; see the notes from the build.)
- When every claim is back, the dispatcher marks the run finished (or, for an evaluation, clears the
  saved threads of copies held for sign-off).

### The backend

| Operation | What it does |
|---|---|
| `start_dispatch` | `POST /api/dispatches` with N: asks the service through `backend/agent_service.py`; 409 while a run is going, 503 if the service can't be reached |
| `start_evaluation` | `POST /api/evaluations` with N, the set and `tiny`: the same, for an evaluation |
| `create_run` | `POST /api/runs` with kind, N, set, `tiny`, model, effort: the service creates the run; an evaluation's copies are made here |
| `list_run_claims` | `GET /api/runs/{run_id}/claims`: the claims to work, in order |
| `finish_run` | `POST /api/runs/{run_id}/finish`: marks it finished |
| `stop_running_runs` | `POST /api/runs/stop-running`: marks any running run stopped, when the service starts |
| `get_current_run` | the latest dispatch, with its claims, their states, the summary, model cost and time |
| `record_model_call` | `POST /api/claims/{claim_id}/model-calls`: tokens and cost, with the run or none |
| `record_work_time` | `POST /api/claims/{claim_id}/work-times`: seconds, with the run or none |
| `list_evaluations` | `GET /api/evaluations`: newest first, with the measures |
| `get_evaluation` | `GET /api/evaluations/{run_id}`: the run, its measures and each claim beside the handler's decision |

Removed: `set_todos`, `start_run`'s cap, the run's free-text summary and `backend/instructions/queue.md`.

### The service and the dashboard

- The service gains `POST /dispatches` and `POST /evaluations`. Each refuses with 409 while a run is
  going, otherwise answers 202 at once and works the run in a background thread.
- The queue page gets a "Claim agents" picker (1 to 25, starting at 3) and a "Start dispatch" button. The
  run panel lists the dispatch's claims with their states, then the summary when it finishes: counts of
  approved, declined and referred, each payout waiting for sign-off, and each claim left for a handler or
  still in progress, with the model cost and time.
- A new Scoreboard page (`#/scoreboard`) starts an evaluation (set, whole or tiny, N) and lists
  evaluations with every measure. `#/scoreboard/<run_id>` shows its claims beside the handlers', with
  misses marked and each claim linked to its page.
- The instructions page shows only the claim agent's instructions.

## Phase 1: the default model, and cost and time

- [x] The default model is `openai/gpt-6-luna` with low effort; `MODEL_NAME` sets another with no
      effort (test)
- [x] The claim graph's model node records each call's input and output tokens and its cost from
      OpenRouter's `token_usage.cost`, with the run from the config or none (test with a stub reply)
- [x] The harness records each stretch of work's seconds with its run (test)
- [x] A claim held for sign-off, then resumed, has two stretches of work, and the wait between them is
      in neither (test)
- [x] A claim's cost and time from a failed attempt still count toward its run (test)
- [x] `record_model_call` and `record_work_time` follow the API rules (test reads the OpenAPI schema)
- [x] `npm run check:python` passes

## Phase 2: the dispatcher in place of the queue agent

- [x] `agent/dispatcher.py` is a `StateGraph` with `list_claims` and `work_claim`, fanning out with
      `Send`, and no model and no checkpointer
- [x] A dispatch works the claims in progress first, then the waiting claims oldest submitted first
      (test)
- [x] Never more than N claims are worked at once: a stub claim agent records its start and finish, and
      no moment has more than N (test, at N=3)
- [x] Each claim keeps its own thread, and a held claim waits for its sign-off as before (test)
- [x] A dispatch skips a claim the service is resuming after a sign-off (test)
- [x] The queue agent, its tools, `queue.md`, the to-do list and its table and test are gone; `uv run -m
      agent CLM-1001` still works one claim (tests)
- [x] `npm run check` passes

## Phase 3: problems are put right

- [x] A claim that hits a rate limit carries on from its last saved step as the same attempt, with one
      decision (test with a stub that raises `ModelRateLimitError` once)
- [x] A claim still hitting rate limits after 3 tries stays in progress, and the dispatch works the
      other claims and finishes (test)
- [x] A claim that fails with another error goes back to the queue and the same dispatch tries it once
      more; one that fails twice is left for a handler (tests)
- [x] Each claim run gets its own `PostgresSaver` on the process's shared pool of 4 (test on the saver
      and pool settings)
- [x] No claim is decided or paid twice across a retry (test counts decisions and payments)
- [x] `npm run check:python` passes

## Phase 4: starting a dispatch from the dashboard

- [x] `start_dispatch` asks the service with N and returns at once; a second start while one runs is
      409 (tests)
- [x] With no service listening, `start_dispatch` returns 503 and the dashboard says the agent's service
      isn't running (tests)
- [x] When the service starts, any run still marked running is marked stopped (test)
- [x] The run panel's summary is built from the records: counts by decision, payouts waiting for
      sign-off, claims left for a handler or still in progress, model cost and time (test)
- [x] The queue page has the N picker (1 to 25, default 3) and the start button (vitest)
- [x] The end-to-end test starts a dispatch on the dashboard and the stand-in on :8003 receives N
- [x] `npm run check` passes

## Phase 5: evaluations

- [x] Seeding stores the held-out claims and both sets' handler decisions in tables no claim operation
      reads, and drops the old to-do table (test)
- [x] `start_evaluation` makes copies with new ids (such as `E7-CLM-1001`) and works them through the
      same dispatcher; the queue's counts are the same before and after (test)
- [x] `get_claim` on a copy holds nothing from the handlers' decisions (test)
- [x] A copy approved over the limit counts as finished, its saved thread is cleared, and a sign-off on
      it is refused (tests)
- [x] Each evaluation records its set, `tiny`, N, model and effort, and each claim's decision, payout,
      reason, cost and time (test)
- [x] An evaluation that stopped partway shows as stopped (test)
- [x] The measures match ones worked out by hand for a scripted evaluation, with an undecided claim
      counted as a referral (test)
- [x] `npm run check:python` passes

## Phase 6: the scoreboard and Langfuse tags

- [x] The Scoreboard page lists evaluations newest first, with every measure (vitest)
- [x] An evaluation's page shows each claim with the handler's decision and payout beside the agent's,
      misses marked, and a link to the claim (vitest)
- [x] Starting an evaluation on the dashboard reaches the stand-in with N, the set and `tiny` (e2e)
- [x] The scoreboard shows an evaluation the test plays out through the API (e2e)
- [x] A copy's claim page has no sign-off form (vitest)
- [x] Each claim's trace carries its run's tag through `langfuse_tags` (test with the handler stubbed)
- [x] `npm run check` passes with `.env` renamed

## Phase 7: live checks and the documents

- [x] After one `npm run seed`, with `npm run dev` running, a dispatch at N=3 is stopped partway by
      killing everything on port 8002, and `npm run dev` is started again
- [x] A dispatch at N=25 carries on the claims left in progress as the same attempts and works the rest
      25 at once (start times), in well under the sum of its claims' times; per-claim time is close to
      N=3's, or the slowdown is explained
- [x] Claims held for sign-off wait, and a sign-off on the dashboard still pays within five seconds
- [x] No claim fails for want of a database connection, and `pg_stat_activity` never shows more than 12
      connections for user postgres during the dispatch
- [x] An evaluation of the evaluation set at N=25 and one of the held-out set's 5 tiny claims at N=5
      show on the scoreboard with every measure, and the queue's counts are unchanged
- [x] Through Langfuse's API, the N=25 dispatch's claims are found together by its tag
- [x] `docs/SPEC.md` (working the queue, and what the team needs to see), `docs/TECH_STACK.md`,
      `docs/PROCESS.md` (these runs replace the five-claim cap), `docs/CODE_STANDARDS.md` and
      `docs/STATUS.md` describe the product as it now is
- [x] `npm run check` passes

## Notes from the build

- **The worker retries rate limits itself, not with `RetryPolicy` and `error_handler`.** In LangGraph
  1.2.12 a node's `error_handler` doesn't catch a failure when several `Send` tasks run in parallel: the
  handler runs, but the error still stops the graph (checked with a small script, with and without a
  checkpointer). A dispatch must never stop for one claim, so `work_claim` catches the two errors itself,
  waits (1, then 2 seconds) and calls the harness again, which carries the claim on from its last saved
  step as the same attempt. After 3 tries it leaves the claim in progress. The result is what the brief
  asks for; only the mechanism differs.
- **The claim agent's own retry wrapper is gone**, as choice 3 says. Other model errors fail the claim at
  once, and the harness tries it once more on a new thread.
- **Phases 1 to 6 were built together and committed as one change**, because the run tables, the
  dispatcher and the dashboard all changed shape at once; each phase's checks were run before the commit.
- **The live N=25 dispatch found a race in the shared pool.** 25 workers asked for the pool at the same
  moment and each made its own, so Postgres reached 26 connections. The pool is now made once under a
  lock (with a test from 25 threads), and the N=25 evaluation afterwards peaked at 10.
- **Where the time goes at N=25.** A claim worked from the start took 24.3 seconds on average at N=25,
  against 16.5 at N=3. Timing the backend directly, a call takes about 0.27 seconds with 25 at once
  against 0.04 with 3: the requests wait for the backend's 4 database connections, and a claim makes
  30 to 40 of them (its steps, model calls and tools). Keeping to 4 connections, the way to win this
  back would be fewer, larger backend calls per claim, such as posting a node's steps together.
- **The start times** come from each claim's first saved checkpoint in Postgres, since the dispatcher
  keeps none of its own.
