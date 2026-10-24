# Plan: the agent's harness

The brief is `docs/HARNESS.md`. One run of the UnderCover Agent works the whole queue, up to a cap. The
agent plans the run as a to-do list, hands each claim to a fresh claim agent, looks up only the sections
of the wording and the manual that it needs, and recovers from failures on its own. `docs/PLAN.md` is the
pilot's finished plan and stays as it is.

## Choices for you

Review these first. The documents in `docs/` don't make these decisions.

1. **Our own to-do tool, not LangChain's `TodoListMiddleware`.** The middleware has only three states
   (pending, in progress, completed) and keeps the list inside the agent. The brief needs a fourth state,
   "left for a handler", and the dashboard must show the list, so the backend stores it.
2. **The backend owns the run rules.** The cap, the second-failure rule and the check for missed claims
   are refusals in the backend, like the $1,000 rule. A confused agent then cannot break them.
3. **The fresh helper is a second `create_agent` call, run as a tool.** The queue agent has a
   `work_claim` tool. Each call builds a new claim agent with a new thread, and the tool returns one line.
   This is the pilot's `run()` with new tools, so no new framework comes in.
4. **One claim at a time.** The queue agent hands out claims one by one. Parallel claim agents would be
   faster but would add locking to SQLite. We can add them later.
5. **A new claim state, `needs_handler`** ("Left for a handler"), for a claim that failed twice. It shows
   in Raspberry, the fifth brand color.
6. **Every claim starts inside a run.** `start_claim` takes a `run_id`. Running one named claim
   (`uv run -m agent CLM-1001`) makes a run with a cap of 1 and calls the claim agent directly, without the
   queue agent.
7. **Claims stuck from an earlier run go back in the queue.** When a run starts, any claim still
   `in_progress` goes back to `waiting`, and that counts as a failed attempt.
8. **Sections, not search.** The agent lists a document's numbered sections, then reads the ones it
   needs. Both documents already have numbered `##` headings, so this needs no search index.
9. **Model retries come from `ModelRetryMiddleware`** (two retries with backoff), plus a 60-second
   timeout on the model. A claim that still fails is released by the `work_claim` tool.
10. **Browser checks use the Chrome DevTools MCP server**, as in the pilot, because the Playwright MCP
    server is not connected.

## Approach

### Claim states

| State | Set when |
|---|---|
| `waiting` | seeded, or released after a first failure |
| `in_progress` | a claim agent starts the claim |
| `waiting_for_sign_off` | approved over $1,000 |
| `decided` | approved up to $1,000, declined, referred, or signed off |
| `needs_handler` | released after a second failure |

A new `attempts` column counts failures. A claim that is decided is never released, so it can never be
decided or paid twice. The backend already refuses a second decision and a second payment.

### New and changed API

Every route keeps an `operation_id`, a one-line docstring and no optional inputs.

| Operation | Route | Used by |
|---|---|---|
| `start_run` | `POST /api/runs` (takes `cap`) | runner |
| `get_current_run` | `GET /api/runs/current`: the latest run with its to-do list and summary | dashboard |
| `list_waiting_claims` | `GET /api/claims/waiting` | queue agent |
| `set_todos` | `PUT /api/runs/{run_id}/todos` (the whole list: claim id, text, state) | queue agent |
| `finish_run` | `POST /api/runs/{run_id}/finish` (takes `summary`) | queue agent |
| `start_claim` | `POST /api/claims/{claim_id}/start` (now takes `run_id`) | claim agent runner |
| `release_claim` | `POST /api/claims/{claim_id}/release` (takes `error`) | `work_claim` tool |
| `list_sections` | `GET /api/documents/{document}/sections` | claim agent |
| `read_section` | `GET /api/documents/{document}/sections/{number}` | claim agent |
| `get_instructions` | `GET /api/instructions`: now `queue` and `claim` texts | agents, dashboard |

`document` is `policy_wording` or `claims_manual`. `get_policy_wording` stays for the dashboard, but the
agent no longer has it as a tool. The to-do states are `to_do`, `in_progress`, `done` and
`left_for_handler`.

The backend refusals:

- `start_claim` refuses a new claim once the run has started `cap` different claims.
- `release_claim` refuses a claim that is not `in_progress`.
- `finish_run` refuses while a claim is still `waiting` and the run is under its cap. The refusal names
  the claims, so the agent goes back and works them.

### The two agents

- **Queue agent**: tools `list_waiting_claims`, `set_todos`, `work_claim` and `finish_run`. Its
  instructions are `backend/instructions/queue.md`.
- **Claim agent**: tools `get_claim`, `get_policy`, `get_claim_history`, `list_sections`,
  `read_section`, `record_decision` and `pay_claim`. Its instructions are `backend/instructions/claim.md`,
  which replaces `backend/instructions.md`. As in the pilot, it posts every step to its claim.
- **`work_claim(claim_id)`** starts the claim agent on a new thread. On success it returns the claim
  agent's last line. On any error it calls `release_claim` and returns `failed: <error>`, so one bad claim
  never stops the run.

I write both sets of instructions from the policy wording and the manual only. I never open
`data/evals/`.

## Phase 1: runs, the to-do list and recovery in the backend

- [x] `start_run`, `get_current_run`, `set_todos` and `list_waiting_claims` work, with tests
- [x] `start_claim` with a `run_id` refuses a sixth claim on a run capped at five (test)
- [x] `release_claim` once sends a claim back to `waiting`; twice sends it to `needs_handler` (test)
- [x] `release_claim` refuses a claim that is `decided` or `waiting_for_sign_off` (test)
- [x] `start_run` sends a claim stuck `in_progress` back to `waiting` and counts the attempt (test)
- [x] `finish_run` refuses while a waiting claim remains under the cap, and accepts once none does (test)
- [x] `uv run pytest backend -q --no-cov` and `npm run check:python` pass

## Phase 2: documents by section, two sets of instructions and the spec

- [x] `list_sections` for `claims_manual` returns its 14 numbered sections, and for `policy_wording` its 12 (test)
- [x] `read_section` for `claims_manual` section 9 returns "Repeat claims" and nothing from section 10 (test)
- [x] An unknown document or section returns 404 (test)
- [x] `GET /api/instructions` returns `queue` and `claim` texts from `backend/instructions/` (test)
- [x] The claim instructions tell the agent to look up the manual and the wording by section, and the
      queue instructions describe the to-do list, `work_claim`, retries and the final check
- [x] `docs/SPEC.md` takes the claims manual out of "Not for now" and says decisions follow the wording
      and the manual
- [x] `npm run check:python` passes

## Phase 3: the claim agent

- [x] A stub-model test runs the claim agent on CLM-1001: it lists and reads sections, approves $420.00,
      pays, and every step is recorded on the claim
- [x] `GET /api/claims/CLM-1001` returns `sections_read`, such as `claims_manual 6` and `policy_wording 3`,
      taken from the claim's steps (test)
- [x] A stub model that raises an error makes `work_claim` return `failed: ...` and release the claim (test)
- [x] The $1,000 sign-off test from the pilot still passes on the claim agent
- [x] `test_model_name`: `deepseek/deepseek-v4.1-flash` with low effort by default, `MODEL_NAME` when set
- [x] No file in `agent/` reads `data/` (the pilot's test still passes)
- [x] `npm run check:python` passes

## Phase 4: the queue agent and the runner

All these tests use stub models for both agents.

- [x] The queue agent writes a to-do list, works each claim through `work_claim`, ticks items off, and
      finishes with a summary that `get_current_run` returns (test)
- [x] A run capped at two claims works two claims and leaves the rest `waiting` (test)
- [x] A claim whose claim agent fails does not stop the run: the next claim is still worked (test)
- [x] A claim that fails once goes back in the queue and is decided on the second try (test)
- [x] A claim that fails twice ends `needs_handler`, with its to-do item `left_for_handler` (test)
- [x] A queue agent that tries to finish early gets the refusal, works the missed claim, then finishes (test)
- [x] `npm run agent -- --cap 5` works a capped run, and `uv run -m agent CLM-1001` works one claim
      (the runner is tested with stubs here; the live run is in phase 6)
- [x] `npm run check:python` passes

## Phase 5: the dashboard

- [x] The queue page shows the current run: its cap, the to-do list with each item's state, and the
      summary once it finishes. It refreshes with the queue every five seconds (vitest)
- [x] The queue counts and chips include "Left for a handler" (vitest)
- [x] The claim page lists the sections the agent read (vitest)
- [x] The instructions page shows the queue instructions and the claim instructions, read only (vitest)
- [x] An e2e test drives a run through the API (no model): it starts a run, sets a to-do list, and sees
      it on the queue page without a reload
- [x] In the browser (Chrome DevTools MCP), light and dark mode: the run panel, the new chip and the
      sections read look right
      (checked with screenshots from Playwright's Chromium during the live run, as Chrome DevTools could
      not connect to Chrome)
- [x] `npm run check:ts` and `npm run e2e` pass, coverage at or above 80%

## Phase 6: a live run and the hand-over

This is the only phase that calls the real model. It costs a few cents on DeepSeek V4.1 Flash.

- [x] Delete `backend/undercover.db`, start `npm run dev`, and run `npm run agent -- --cap 5`
- [x] In the browser, the to-do list fills in and is ticked off, and each claim's steps show the sections
      it read
- [x] Five claims end `decided`, `waiting_for_sign_off` or `needs_handler`, and the other 20 stay
      `waiting` (checked with `GET /api/claims`)
- [x] The run's summary is on the queue page
- [x] `docs/TECH_STACK.md` and `docs/STATUS.md` describe the product as it now is
- [x] `npm run check` passes in full

## Not in this build

- Scoring the agent against `data/evals/`.
- Claim agents in parallel, and starting a run from the dashboard.
- Sign-in, real payments and messages to customers, as `docs/SPEC.md` says.
