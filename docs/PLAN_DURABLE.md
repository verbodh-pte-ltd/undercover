# Plan: approvals that wait, and work that survives a restart

The brief is `docs/DURABLE_LOCAL.md`, our copy of `docs/DURABLE.md` for this machine. The claim agent
becomes a LangGraph graph that saves its progress after every step, waits for a handler's sign-off on a
payout over $1,000, and carries on after a restart. The payout rules and Langfuse tracing come in first.

DATABASE NOTE: We use Postgres running locally in Docker, not Supabase. Don't look for a Supabase project,
the Supabase MCP server or a session pooler. The database is the `undercover-postgres` container
(Postgres 17) on `127.0.0.1:5432`, and `DATABASE_URL` in `.env` already points at it. Wherever the
original brief says Supabase, read local Postgres.

SIDENOTE: For consistency with the course, please read `labs/week2/week2day3.ipynb` and build the solution
in the same style as the lab. Where the lab and this brief differ, follow the brief.

## Choices for you

Review these first. The documents in `docs/` don't make these decisions.

1. **The backend gives each try at a claim its thread.** `start_claim` sets `thread_id` to the claim id
   and the try number, such as `CLM-1003-1`. A claim that was stopped keeps its thread; a claim released
   after an error gets a new one (`CLM-1003-2`) and starts afresh. The same id is the Langfuse session.
2. **The decision stays a tool.** The claim agent keeps `record_decision`, as in the product today, and
   the lab's `decide` node is not used. When the model stops calling tools, a `settle` node reads the claim
   from the backend and picks the next node: `sign_off`, `pay` or the end. The graph never compares the
   payout with the limit; it follows the claim's status.
3. **Sign-off reaches the graph through the backend.** After a sign-off the claim is `in_progress` again,
   with `signed_off_by` and `signed_off_at` set and nothing paid. The graph resumes with
   `Command(resume={"handler_id": ...})` read from the claim.
4. **A claim counts as decided when it has a decision**, not by its status, because a signed-off claim
   is `in_progress` with a decision. `record_decision` and `pay_claim` check that.
5. **The queue agent gets the checkpointer too**, on thread `run-<run_id>`, still built with
   `create_agent`. A stopped run is carried on by resuming that thread. This is one more argument to
   `create_agent`, and the brief asks for a stopped run to be finished.
6. **`start_run` no longer releases claims stuck `in_progress`.** They are carried on instead, which is
   what the brief asks for. A new `list_unfinished_claims` operation lists them for the runner.
7. **Tests use LangGraph's `InMemorySaver`.** A "restart" in a test is a new graph built on the same
   saver. Tests stay on SQLite with no network, and nothing from `.env` reaches them.
8. **`.env` is read only by the npm scripts.** `npm run dev`, `npm run agent` and `npm run seed` use
   `uv run --env-file .env`. `agent/main.py` stops calling `load_dotenv` on import, so importing it in a
   test can't bring in `DATABASE_URL`. `uv run -m agent` still loads `.env` in its `__main__` block.
9. **Connections:** the backend's SQLAlchemy pool is 4 with no overflow. The agent service and each
   `uv run -m agent` process have a psycopg pool of 2 for the checkpointer. With the backend, the service
   and one run, that is at most 8.
10. **`npm run seed`** empties our tables and the checkpointer's, then seeds from `data/`. Old threads
    must go with the old claims, or `CLM-1001-1` would carry on yesterday's work.
11. **The agent service** is FastAPI on port 8002 with one operation, `resume_claim`. It answers at once
    and carries on the claim in a background thread. The backend calls it from a FastAPI background task
    after the sign-off is saved, with a 5-second timeout, and ignores a failure.
12. **The end-to-end stand-in** for the agent service is `e2e/agent-stand-in.mjs` on port 8003. It records
    each call, and the test reads them back from it.
13. **Browser checks use the Chrome DevTools MCP server**, because the Playwright MCP server is not
    connected here.
14. **Model calls retry** with `with_retry` (two retries) and the 60-second timeout, in place of
    `ModelRetryMiddleware`, which only works inside `create_agent`.

## Approach

### The claim graph

State: `messages` (with `add_messages`), `claim_id`, `model_calls`.

| Node | What it does |
|---|---|
| `agent` | Calls the model with the claim instructions and the tools. Refuses a 31st model call by raising an error, so `work_claim` releases the claim. |
| `tools` | `ToolNode` with `get_claim`, `get_policy`, `get_claim_history`, `list_sections`, `read_section`, `calculate_payout`, `record_decision`. Refusals come back as text, as today. |
| `settle` | Reads the claim. Approved and `waiting_for_sign_off`: go to `sign_off`. Approved otherwise: go to `pay`. Anything else: end. |
| `sign_off` | `interrupt({"claim_id", "payout"})`. On resume, records nothing and goes to `pay`. |
| `pay` | `pay_claim` with the approved payout. The backend treats a repeat as done. |

Edges: `START → agent`; `agent → tools` when the model asked for tools, else `settle`; `tools → agent`;
`sign_off → pay`; `pay → END`. Every run uses `durability="sync"`. Each update from the stream is posted
as steps on the claim, as today, so a resumed graph adds only the steps it takes.

### The backend

| Operation | Change |
|---|---|
| `work_out_payout` | New, `POST /api/payouts`: policy id, benefit, lines and `paid_by_others`, all required. The rules move from `labs/week2/week2day1_payout.py` to `backend/payout.py`, with the limit from `data/company.json`. |
| `start_claim` | Sets `thread_id` on a new try, and returns the same thread for a claim already `in_progress`. |
| `release_claim` | Also clears `thread_id`. Refuses a claim that has a decision. |
| `start_run` | No longer releases stuck claims. |
| `list_unfinished_claims` | New, `GET /api/claims/unfinished`: the claims `in_progress`, signed-off ones included. |
| `record_decision` | The same decision again returns the claim. A different one is still 409. |
| `pay_claim` | Paying the same amount again returns the claim. Records `paid_at`. Sets the status to `decided`. |
| `sign_off` | Records `signed_off_by` and `signed_off_at`, sets `in_progress`, no longer pays, then calls the agent service. |

The store moves to SQLAlchemy Core. With `DATABASE_URL` set, it connects to Postgres with
`search_path=undercover` and creates the schema; without it, it uses the SQLite file as today.

### The agent service and the runner

- `agent/service.py`: `POST /claims/{claim_id}/resume` (`resume_claim`). `npm run dev` starts it beside
  the backend and the dashboard.
- `uv run -m agent` first carries on each unfinished claim, then resumes a run that has no summary, and
  only then starts a new run.
- Langfuse: when its keys are set, each graph run gets a `CallbackHandler` with the claim's thread id as
  its session id. Without keys there is no tracing, so tests need no network.

## Phase 1: the payout rules

- [x] `backend/payout.py` holds the rules, and `POST /api/payouts` returns the calculation, the payout
      and `needs_sign_off` (test)
- [x] For a lost suitcase where a camera and its lens are one set, the operation returns the plan's
      per-item limit for that line, not two items (test)
- [x] The approval limit comes from `data/company.json`: a test that raises it changes `needs_sign_off`
- [x] The operation has an `operation_id`, a one-line docstring and no optional inputs (test reads the
      OpenAPI schema)
- [x] The claim agent has a `calculate_payout` tool, and `claim.md` tells it to use it for every approval
- [x] A stub-model test runs the claim agent through `calculate_payout` and `record_decision`
- [x] `npm run check:python` passes

## Phase 2: threads and tracing

- [x] `start_claim` sets `thread_id` to `CLM-1001-1` on a first try, and keeps it on a second start while
      the claim is `in_progress` (test)
- [x] After `release_claim`, the next start gives `CLM-1001-2` and clears the old steps (test)
- [x] With Langfuse keys unset, the agent adds no Langfuse handler (test)
- [x] With keys set, the config for a claim carries a `CallbackHandler` and the session id `CLM-1001-1`
      (test with the handler stubbed)
- [x] Live: one claim worked by `uv run -m agent CLM-1005` shows up in Langfuse under session
      `CLM-1005-1`, read through Langfuse's API (the v2 observations API, filtered by session)
- [x] `npm run check:python` passes

## Phase 3: the database

- [ ] The store uses SQLAlchemy Core, and every existing backend test passes unchanged on SQLite
- [ ] `sqlalchemy` is a main dependency in `pyproject.toml`
- [ ] With `DATABASE_URL` set, the backend creates the `undercover` schema and its tables in local
      Postgres (checked with `docker exec undercover-postgres psql -U postgres -c "\dt undercover.*"`)
- [ ] `npm run seed` reseeds local Postgres: 25 claims, all `waiting`, and no checkpoints
- [ ] The backend's pool is 4 with no overflow (test on the engine's settings)
- [ ] `npm run check` passes with `.env` renamed, and `DATABASE_URL` is not in the environment of any
      test (test)
- [ ] `npm run dev` serves the queue from local Postgres (`GET /api/claims` returns 25 claims)

## Phase 4: the backend for durable work

- [ ] Sign-off records who and when, sets `in_progress`, pays nothing, and a payment over $1,000 is still
      403 before it (tests)
- [ ] After sign-off, `pay_claim` pays, records `paid_at` and sets `decided` (test)
- [ ] The same decision twice returns the claim; a different one is 409 (tests)
- [ ] The same payment twice returns the claim, and the claim is paid once (test)
- [ ] `start_run` leaves a stuck claim `in_progress` with its attempts unchanged, and
      `list_unfinished_claims` lists it (test)
- [ ] Sign-off calls `UNDERCOVER_AGENT` at `/claims/{id}/resume` after saving, and still succeeds when
      nothing answers there (tests with a stubbed client)
- [ ] `npm run check:python` passes

## Phase 5: the claim agent as a graph

- [ ] The claim agent is a hand-written `StateGraph` with the nodes above, and has no `pay_claim` tool;
      `claim.md` no longer asks it to pay
- [ ] Under the limit: a stub-model test decides CLM-1001 and the `pay` node pays it once
- [ ] Over the limit: the graph stops at `sign_off` with the claim `waiting_for_sign_off` and nothing paid
      (test)
- [ ] A sign-off, then a resume, pays the claim once, with the handler and the time on it (test)
- [ ] A claim stopped after its first steps carries on from its last step on a new graph built on the
      same saver, with the same thread, the same attempts and one decision (test)
- [ ] A claim that fails with a model error is released, and its next try has a new thread and starts
      afresh (test)
- [ ] A 31st model call is refused and the claim is released (test)
- [ ] `npm run check:python` passes

## Phase 6: the agent service, the runner and the dashboard

- [ ] `resume_claim` on the service resumes a signed-off claim and pays it (test with a stub model)
- [ ] `uv run -m agent` carries on unfinished claims first, then a run with no summary on its
      `run-<id>` thread, before starting a new run (tests)
- [ ] `npm run dev` starts the backend, the dashboard and the agent service on :8002
- [ ] The dashboard's button reads "Sign off $1,250.00", and a signed-off claim shows "In progress" with
      the handler and the time, then "Paid" once paid (vitest)
- [ ] The end-to-end test signs off on the dashboard and the stand-in on :8003 receives the call for that
      claim
- [ ] `npm run check` passes with `.env` renamed

## Phase 7: live checks and the documents

- [ ] After `npm run seed`, `npm run agent -- --cap 5` on the default model works five claims
- [ ] A claim over the limit waits for sign-off; signed off on the dashboard through the Chrome DevTools
      MCP server, it shows as paid within five seconds, with the handler and the time
- [ ] A run killed after a claim's first steps carries on with the same thread the next time, with the
      attempts unchanged and one decision
- [ ] During the live run, `pg_stat_activity` shows no more than 12 connections for user postgres
- [ ] Through Langfuse's API, the held claim's session has traces from before and after the sign-off
- [ ] `docs/SPEC.md` (its section on working the queue only), `docs/TECH_STACK.md`, `docs/PROCESS.md`
      and `docs/STATUS.md` describe the product as it now is
- [ ] `npm run check` passes
