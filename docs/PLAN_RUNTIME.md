# Plan: sending claims back and the agent on AgentCore Runtime

The brief is `docs/RUNTIME.md`. A senior handler can send a large approval back to the agent with a note,
and the agent decides again. The agent becomes an AgentCore app that runs the same way on the laptop and
on AgentCore Runtime, and the app keeps one piece of the agent's work going at a time, so two Runtime
sessions can never both run a dispatch.

**Sidenote from the brief.** For consistency with the course, read `labs/week3/week3day4_lab2.ipynb`,
which deploys the agent on AgentCore Runtime and points the app at it, without running it. Where the
notebook and the brief differ, follow the brief. The pause in step 1 was in the course's build for
self-improvement, before the security build took it out: `git show week2day5-end:agent/harness.py` has its
`wait_for_sign_off` node, and `git show week2day5-end:agent/service.py` its `resume_claim` route. Lab 1's
`agentcore dev` also serves on port 8080, so stop it before `npm run dev`.

## Where our build differs from the brief's notes

The brief's notes describe the course's `week3day3-end`. Ours took a different path in places, and the
plan follows the brief's intent on our code:

- **The pause is already here.** Our security build kept it: `settle` sends an approval over the limit to
  `sign_off`, which calls `interrupt()`, and `pay` sends a payment the Gateway refused there too
  (`agent/main.py`). The service's `resume_claim` route carries a signed-off claim on, and an evaluation's
  copy already ends instead of pausing. Step 1 changes what the pause answers and when memory is saved,
  rather than adding the pause.
- **Memory is saved before the pause.** Our graph runs `remember` before `settle`, so a decision over the
  limit is saved before anyone signs it off, and a decision later sent back would stay in memory. Step 1
  moves `remember` to the end of the work.
- **Where things are.** No `harness.py`, `runs.py`, `improvement.py`, `database.py` or `dependencies.py`:
  the claim graph, `run`, `carry_on` and `work` are in `agent/main.py`; the runs, rounds, sign-off and
  decisions are in `backend/main.py`, with the tables in `backend/store.py` (a claim's decision is columns
  on its `claims` row); the service's tests are `agent/tests/test_routes.py`; the sign-off panel is
  `dashboard/src/SignOff.tsx`; the agent's stand-in is `e2e/agent-stand-in.mjs`.
- **Our database on AWS is RDS, not Supabase.** The labs give the agent `AWS_DATABASE_URL`, straight to
  port 5432 with TLS, in place of Supabase's transaction pooler (`labs/week3/week3_aws_route.py`).
  Prepared statements off (step 3) works on both. Handlers sign in through Cognito on AWS, which the send
  back uses unchanged.

## Approach

- **The answer lives in the app, not the request.** Signed off or sent back is read from the claim's
  record, so a resume, a retried call and the next dispatch all see the same answer, and a request to the
  agent names only the claim.
- **Every "only once" is a conditional update of one row, checking the row count.** Taking a sent-back
  claim (`sent_back` to `in_progress`), signing off or sending back (only while `waiting_for_sign_off`),
  and starting work: this holds on SQLite, on Postgres and through a transaction pooler, which a check
  followed by an insert doesn't.
- **One slot for the agent's work.** A one-row table, `agent_work`, says what's going (a run or a round)
  and when the app last heard from it. Starting work is one conditional update that takes the slot when
  it's free, or when what holds it has been quiet for five minutes, committed before the app asks the
  agent. Resumes never touch the slot.
- **The agent registers each piece of work as an async task** before it answers, and completes it in a
  `finally`, so Runtime keeps a session while its work goes on and ends it after.
- **Nothing at import.** The agent gets ready on its first request, once per process, behind a lock: the
  credentials from Identity on Runtime, the key check, the pool.
- **Two routes to the agent, one function in the app.** `backend/agent_service.py` sends a payload to
  `UNDERCOVER_AGENT` over HTTP with the key, or to `UNDERCOVER_AGENT_RUNTIME` with boto3, and returns
  taken, refused, or unknown (sent, but no clear answer). Callers undo only on "not taken".

## Phase 1: the claim approval comes back (step 1)

- `agent/main.py`: the graph becomes `agent` → `settle` → `pay` or `sign_off` → `remember` → end, so
  memory is saved once the decision is final. `sign_off` reads the answer it resumes with: signed off
  records "Signed off by" and goes to `remember`; sent back is phase 2's.
- `resume_claim` in the service: reads the claim; refuses one whose thread has no interrupt waiting, with
  the reason; otherwise answers at once and resumes it in the background on the current version, outside
  any run and beside any dispatch. Today's lock-free `dispatcher.take` stays, so the same claim is never
  worked twice in one process.
- `sign_off` in `backend/main.py`: a conditional update from `waiting_for_sign_off`, so a sign-off and a
  send back at the same moment can't both go through. Then it asks the agent to resume; when that call
  fails or times out it logs a warning and the sign-off stands. `docs/STATUS.md` lists the unsaved memory
  as a known gap.

Done when:

- [x] A stand-in test pauses an approval over the limit with its interrupt on the thread
- [x] A stand-in test pauses a payment the Gateway refused
- [x] An evaluation's copy over the limit ends without pausing, and its thread is deleted
- [x] A resume after a sign-off records "Signed off by", ends, and saves the claim to a stand-in memory
      store exactly once; nothing was saved before the sign-off
- [x] `resume_claim` refuses a claim that isn't paused, with the reason
- [x] Two sign-offs at once: one pays, the other gets 409
- [ ] A sign-off whose call to the agent fails answers with the paid claim and logs a warning (the
      answer is tested; the warning comes with phase 3's new `backend/agent_service.py`)
- [ ] With `npm run dev` running, a claim over the limit pauses, and signing it off on the dashboard pays
      it and adds "Signed off by" to its steps (not run: a live check, for phase 5)
- [x] `npm run check:python` passes; committed

## Phase 2: sign off or send back (step 2)

- **The app.** A `sent_back` table: the claim, the decision sent back (decision, payout, calculation,
  section, reason, who decided, when), the note, who sent it back and when. The claim gets status
  `sent_back` and a column `resume_heard_at`.
  - `send_back_claim` (`POST /api/claims/{claim_id}/send-back`, a handler who can sign off over the limit,
    a note of 1 to 1,000 characters): a conditional update from `waiting_for_sign_off`, 409 for an
    evaluation's copy or any other status. In one transaction it keeps the decision in `sent_back`, clears
    the claim's decision, calculation and sign-off fields, records the note as a step and sets
    `sent_back`. Then it asks the agent to resume the claim, logging a warning on a failure or timeout.
  - `take_sent_back_claim` (agent's key only): `sent_back` to `in_progress` as a conditional update, with
    `resume_heard_at` set; only the first caller gets the claim and the note, anyone else gets 409.
  - `record_resume_heard` (agent's key only): sets `resume_heard_at` to now.
  - `list_run_claims` for a dispatch adds the sent-back claims, and leaves out a claim in progress whose
    `resume_heard_at` is under five minutes old. `start_claim` takes a sent-back claim the same way.
  - `get_claim` adds the claim's sent-back decisions, newest first.
- **The agent.** Whoever takes the claim resumes its thread with `Command(resume=...)` carrying the note.
  `sign_off` then returns to `agent` with a human message from the handler, by name, and `model_calls`
  back to 0. The message says to treat the note as a senior handler's review, checked against the policy
  wording and the claims manual, and to refer the claim with the reason when the wording doesn't support
  what it asks. It's a constant in `agent/main.py`, outside the instructions. A resume tells the app every
  minute while it works.
- **`backend/instructions/claim.md`** changes only where it says a decision can't be changed.
- **The dashboard.** `SignOff.tsx` adds Send back with a required note (1,000 characters, counted). The
  queue shows "Sent back". The claim page shows each sent-back decision with its note, who and when, beside
  the current decision, and says when a claim waits for the next dispatch. Plain words, `docs/BRAND.md`.

Done when:

- [ ] Tests: send back refused for a handler who can't sign off over the limit (403), for an evaluation's
      copy and for a claim not waiting for sign-off (409), and for an empty or 1,001-character note (422)
- [ ] A sign-off and a send back at the same moment: exactly one goes through
- [ ] The decision is kept with its note, who and when, and the claim's own decision is cleared
- [ ] A sent-back claim asked for by a resume and a dispatch at once is taken once
- [ ] A dispatch started during a resume leaves its claim alone; one after five quiet minutes takes it
- [ ] A stand-in-model test: the new decision within the limit is paid; one over the limit pauses again
- [ ] A send back the agent never received is worked by the next dispatch, which resumes the paused
      thread with the note
- [ ] Vitest covers the Send back form, its required note and the sent-back history on the claim page
- [ ] With `npm run dev` running, a claim sent back with a note is decided again, and the claim page shows
      both decisions and the note, checked through the Playwright MCP server
- [ ] `npm run check:python` and `npm run check:ts` pass; committed

## Phase 3: the agent as an AgentCore app (step 3)

- **`agent/entrypoint.py`**: `app = BedrockAgentCoreApp()` with one `@app.entrypoint`. Each payload names
  an operation: `start_dispatch`, `start_evaluation`, `start_round`, `answer_proposal` (phase 4 replaces
  these four with `work_run`, `work_round` and `carry_round_on`), `resume_claim` and `health`. Each checks
  its inputs with a Pydantic model (400 naming what's wrong, as does an unknown operation), registers an
  async task, starts a thread that completes it in a `finally`, and answers `{"accepted": true}`, or
  `{"refused": "<reason>"}` where the service answered 409. `health` answers `{"healthy": true}`.
- **Getting ready** on the first request, behind a lock, retried on the next request if it fails: on
  Runtime (any `AGENTCORE_CREDENTIAL_<NAME>_NAME` set) reseed `random` and fetch each credential with
  `requires_api_key` into `<NAME>`; then the key check, the pool, and today's "stop what was running"
  (phase 4 takes that out).
- **The key off Runtime**: every operation but `health` needs `Authorization: Bearer <UNDERCOVER_API_KEY>`
  (401 otherwise, compared with `secrets.compare_digest`). On Runtime none is asked for.
- **`agent/service.py` goes**, with its tests moved to `agent/tests/test_entrypoint.py`. `npm run dev`
  serves `agent.entrypoint:app` with uvicorn on 127.0.0.1:8080 with reload.
- **`backend/agent_service.py`**: posts `/invocations` at `UNDERCOVER_AGENT` (default
  `http://127.0.0.1:8080`) with the key and a 10-second timeout, or, with `UNDERCOVER_AGENT_RUNTIME` set,
  `invoke_agent_runtime` in the app's region with one attempt, 5 seconds to connect and 20 to read, and a
  new session id of 33 characters or more. It answers taken, refused or unknown, as the brief's item 7 sets
  out: a refusal passes on as 409, an unknown tells the handler to reload the page.
- **The pool**: `prepare_threshold=None`.
- **`pyproject.toml`**: `bedrock-agentcore` in the main dependencies; a `runtime` group with what the agent
  needs on Runtime, `bedrock-agentcore` included; `boto3` in `server`. `npm run check:runtime` imports
  `runtime.main` with only that group, in a temporary environment, and joins `npm run check`. Ruff and
  pyright cover `runtime/`. `agentcore/` and `runtime/` stay as the platform team wrote them.

Done when:

- [ ] `TestClient` tests with stand-ins: importing the agent opens nothing; on Runtime the first request
      fetches each credential into its setting once
- [ ] Each operation accepts work and refuses it; a payload with an input missing gets 400
- [ ] The ping says `HealthyBusy` while work goes and `Healthy` once it finished or failed
- [ ] Off Runtime a request without the key gets 401, `health` doesn't need it; on Runtime no key is asked
- [ ] The app's two routes with stand-ins: the key and the session id; the payload; accepted; refused passed
      on as 409; a failed connection and an error answer change nothing and say so; a timeout or a cut-off
      answer changes nothing
- [ ] `npm run check:runtime` passes inside `npm run check`
- [ ] With `npm run dev` stopped, `uv run --env-file .env --isolated --only-group runtime python -m
      runtime.main` serves on port 8080, and `{"operation": "health"}` posted to `/invocations` answers
      `{"healthy": true}`
- [ ] The end-to-end stand-in answers `/invocations` and `/ping`; `npm run check` passes; committed

## Phase 4: the app keeps one run at a time (step 4)

- **The slot.** `agent_work` (one row): `kind` (`run` or `round`, or empty), the id, `started_at` and
  `heard_at`. `start_dispatch`, `start_evaluation`, `start_round`, `approve_proposal` and
  `reject_proposal` take it with one conditional update, free or quiet for five minutes. Taking it from
  quiet work marks that run or round stopped with "The agent stopped answering", a round with its running
  evaluation. A refusal is 409 naming what's going. A round waiting for a handler's review frees the slot.
- **The app does the first part**, committed before it asks: the run (with its copies), the round, or the
  review recorded. Then `work_run`, `work_round` or `carry_round_on` with the id. When the agent didn't
  take the work, the app undoes it: the run stopped with the reason, the new round stopped, the review
  cleared, the slot freed.
- **The agent** no longer creates runs, rounds (apart from a round's own evaluations, which hold the
  round's slot) or reviews; it reads a proposal's review and reason from the app. It refuses a run that
  isn't running or a round that isn't open. Each piece of work calls `record_work_heard` every minute
  (a round's word covers its evaluation). On failure it stops only its own run or round by id. It keeps no
  lock of its own, and no longer stops anything when it gets ready.
- **Runs and rounds** get `stopped_reason`; the dashboard shows it on a stopped run or round.
- **The end-to-end stand-in** gets the app's address and the agent's key, and finishes or stops the work
  it's given, so later starts aren't refused.

Done when:

- [ ] A second start is refused (409, naming it) while a dispatch, an evaluation or a working round is going
- [ ] Two starts at the same moment: one goes ahead
- [ ] A start after five quiet minutes stops the old work with "The agent stopped answering" and goes ahead
- [ ] A start the agent didn't take is undone; a timeout leaves it going
- [ ] A review the agent didn't take is cleared and the proposal waits again
- [ ] A failed run stops only itself; a resume runs beside a dispatch
- [ ] `e2e/rounds.spec.ts` and the other end-to-end tests pass with the app creating runs, rounds and
      reviews
- [ ] With `npm run dev` running, a dispatch at N=25 works the queue, and starting another while it runs
      is refused on the dashboard with the reason
- [ ] `npm run check` passes; committed

## Phase 5: the live checks and the documents

Run with `npm run dev`, on the default model, on the direct route and with memory off: blank
`UNDERCOVER_GATEWAY_URL` and `UNDERCOVER_MEMORY_ID` in the shell for the run (`.env` has both from the
Day 3 lab), and stop Lab 1's `agentcore dev` first. Reseed once.

- [ ] `npm run check` passes with `.env` moved out of the way
- [ ] A dispatch at N=25 works the queue
- [ ] Signed in as Dana Whitfield: one claim over the limit sent back with a note and decided again; another
      signed off and paid; both claim pages checked through the Playwright MCP server
- [ ] An evaluation of the evaluation set's five tiny claims
- [ ] What the runs spent, from the costs in the database
- [ ] The agent answers its health check through `runtime/main.py` on the laptop
- [ ] `docs/SPEC.md` (the send back), `docs/TECH_STACK.md` (the AgentCore app, the two routes, Deployment
      with the agent on Runtime and our RDS database), `docs/PROCESS.md`, `docs/CODE_STANDARDS.md` (the
      service becomes the AgentCore app) and `docs/STATUS.md` describe the product as it now is; committed

## Not for now

- A Runtime session for each claim.
- Langfuse on Runtime.
- Stopping a Runtime session from the dashboard.
- Deploying: nothing in this build creates anything on AWS. Lab 2 deploys it.
