# Plan: the agent's harness

> Built as planned, then reworked after a human review (`docs/humanreview_harness.md`). The agent is now `agent/backend.py`, `agent/tools.py` and `agent/harness.py`, not the `claim.py`, `queue.py`, `runs.py` and `model.py` named below, and both instructions are goal-based rather than step lists.

Builds what `docs/HARNESS.md` asks for: one run that works the whole queue, a to-do list the team can watch, a fresh subagent for each claim, the claims manual looked up section by section, automatic recovery from failures, DeepSeek V4.1 Flash as the default model, and readable instructions for both agents. Six phases, each building on the last.

## Approach

- **Two agents, one harness, no new framework.** Both are LangChain `create_agent` agents, as in the pilot. The *queue agent* plans the run and hands claims out; the *claim agent* is the pilot's agent, now a subagent that starts from a clean thread for each claim and replies in one line. The queue agent calls it through a `work_claim` tool, so the queue agent's context only grows by one line per claim, not by every document the claim agent read.
- **The backend owns the guarantees, the agent owns the plan.** The model chooses the order and keeps the to-do list. Anything that must always hold is enforced in code, in the backend, where a test can prove it:
  - the cap: `start_claim` refuses a new claim once the run has taken as many as its cap;
  - no double decisions or payments: the pilot's 409s stay;
  - retries: a failed claim goes back to `waiting`, and on its second failure to a new status, `needs_handler`;
  - missed claims: finishing a run is refused while a waiting claim is within the cap, and the refusal goes back to the agent so it carries on.
- **Failures are caught at one point.** `work_claim` runs the claim agent inside one `try/except Exception`. That is the one broad handler in the harness, and it is needed: the brief says one bad claim must never stop the run. It records the failure on the claim (with the error as a step) and returns a one-line report. A claim agent that stops without recording a decision counts as a failure too. Model provider errors and timeouts are first retried by LangChain's `ModelRetryMiddleware`, so a passing blip does not cost a claim an attempt.
- **Documents by section, like a handler with a table of contents.** The backend splits the policy wording and the claims manual at their numbered `##` headings. The agent gets the contents of both, then reads the sections it needs one at a time. `get_policy_wording` (the whole document) goes away. The step logger records each section read as a step of kind `read`, which the claim page lists under "What the agent read".
- **Our own to-do tool, stored in the backend.** LangChain's `TodoListMiddleware` keeps the list in the agent's state with three statuses. We need a fourth (left for a handler) and the list in the backend for the dashboard, so the queue agent gets a small `update_todos` tool that replaces the run's list through the API. The dashboard polls it, as it already polls steps.
- **Every run has a record.** A run (queue or one named claim) gets a `run_id`, a cap, its to-do list and its summary. Running one named claim from the claim page is a run with a cap of 1 that calls the claim agent directly, with the same failure handling.
- **Crashes don't leave claims stuck.** On startup the backend treats any claim still `in_progress` as a failed attempt and closes any unfinished run, so a killed process can't strand a claim or block the next run.

## Claim statuses

| Status | Meaning | Set when |
|---|---|---|
| `waiting` | In the queue | Seeded, or after a first failure |
| `in_progress` | The claim agent is working on it | `start_claim` |
| `awaiting_signoff` | Approved with a payout over $1,000 | The decision is recorded |
| `decided` | Approved and paid, declined or referred | Decision recorded, or sign-off |
| `needs_handler` | Failed twice before a decision; a handler takes it | The second failure |

A claim that already has a decision is never put back in the queue: `fail_claim` leaves it as it is.

## To-do statuses

`to_do`, `in_progress`, `done`, `for_handler`. The list is replaced whole on each update, and each item is `{text, status}`.

## API changes

| Method and path | `operation_id` | Used by | Change |
|---|---|---|---|
| `GET /api/documents` | `list_documents` | agent | New: section numbers and titles of the wording and the manual |
| `GET /api/documents/{document}/sections/{section}` | `read_section` | agent | New: one section, `document` is `policy_wording` or `claims_manual` |
| `GET /api/policy-wording` | `get_policy_wording` | | Removed |
| `GET /api/instructions/{agent}` | `get_instructions` | agent, dashboard | Now takes `queue` or `claim` |
| `POST /api/runs` | `create_run` | agent runner | New: body `{cap}`, records a run; 409 if a run is going |
| `POST /api/runs/start` | `start_run` | dashboard | New: body `{cap}`, starts the queue agent in the background; 409 if a run is going |
| `GET /api/runs/latest` | `get_latest_run` | dashboard | New: the latest run with its to-do list and summary, or `null` |
| `PUT /api/runs/{run_id}/todos` | `update_todos` | queue agent | New: replaces the run's to-do list |
| `POST /api/runs/{run_id}/finish` | `finish_run` | queue agent | New: body `{summary}`; 409 naming the missed claims if any waiting claim is within the cap |
| `POST /api/runs/{run_id}/stop` | `stop_run` | agent runner | New: body `{summary}`; closes a run that could not finish |
| `POST /api/claims/{claim_id}/start` | `start_claim` | agent runner | Now takes `{run_id}`; 409 when the run's cap is used up |
| `POST /api/claims/{claim_id}/fail` | `fail_claim` | agent runner | New: body `{error}`; back to `waiting`, or `needs_handler` on the second failure |
| `POST /api/claims/{claim_id}/run` | `run_agent` | dashboard | Now a run with a cap of 1 on this claim |

## Files

- `backend/documents.py`: split Markdown into numbered sections; the two document routes.
- `backend/run_store.py` and `backend/run_routes.py`: runs, to-do lists, finishing; `backend/runs.py` keeps starting agents in the background.
- `backend/instructions/queue.md` and `backend/instructions/claim.md` replace `backend/instructions.md`.
- `agent/claim.py`: the claim agent (today's `agent/main.py`, renamed) and `work_claim`'s failure handling.
- `agent/queue.py`: the queue agent and its tools.
- `agent/model.py`: the model and the middleware both agents share.
- `dashboard/src/RunPanel.tsx` (run control and to-do list), `dashboard/src/SectionsRead.tsx`.

---

## Phase 1: Documents by section, and the new model

Seeds the claims manual, serves both documents by section, and makes DeepSeek V4.1 Flash the default.

- `backend/seed.py` also loads `data/claims_manual.md` (still an explicit list; `data/evals/` and `data/security/` never opened).
- `backend/documents.py`: `sections(markdown)` splits at `## <n>. <title>`; `list_documents` and `read_section` routes; 404 for an unknown document or section.
- Remove `get_policy_wording` from the backend and the agent's tools; add `list_documents` and `read_section` tools in `agent/tools.py`.
- `agent/model.py`: `DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"` with low reasoning effort; `MODEL_NAME` still overrides it. A request timeout so a hung call fails rather than waits.
- `describe()` logs a `read_section` call as a step of kind `read`, such as `Claims manual 9. Repeat claims`.

**Success criteria**
- [x] `uv run pytest backend agent` passes, with tests that the wording splits into 12 sections and the manual into 14, that `read_section` returns one section's text, and that an unknown section is 404.
- [x] `GET /api/documents` returns both documents with numbers and titles; `GET /api/documents/claims_manual/sections/9` returns "9. Repeat claims" and nothing from section 10.
- [x] `GET /api/policy-wording` is 404.
- [x] A test on a stub model shows a `read_section` call logged as a `read` step.
- [x] A test shows `model()` uses `deepseek/deepseek-v4.1-flash` with low effort, and `MODEL_NAME` when set.
- [x] The seeding test still proves `data/evals/` is never read.

## Phase 2: Runs, the cap, the to-do list and recovery in the backend

The backend records runs and enforces the guarantees, before any agent uses them.

- Schema: `runs (run_id, cap, started_at, finished_at, summary)`, `run_claims (run_id, claim_id)`, `todos (run_id, position, text, status)`, and a `failures` count on `claims`. Reseed after this phase (delete `backend/undercover.db`).
- `start_claim` takes `{run_id}`: refuses a claim that isn't waiting (as now) and a new claim once the run has taken `cap` claims; records the claim against the run.
- `fail_claim`: on an `in_progress` claim with no decision, adds the error as a step, counts the failure, and moves it to `waiting`, or to `needs_handler` on the second failure. A decided claim is left as it is.
- `update_todos`, `get_latest_run`, and `finish_run`, which refuses while the run has cap left and claims are waiting, naming them in the 409.
- Startup recovery: claims left `in_progress` are failed with "Interrupted", and unfinished runs are closed.

**Success criteria**
- [x] `uv run pytest backend` passes, with tests for each point below.
- [x] A run with a cap of 2 can start two claims; the third `start_claim` is 409; a claim already in the run can be started again after a failure.
- [x] `fail_claim` once returns the claim to `waiting` with `failures` 1; twice makes it `needs_handler`; on a decided claim it changes nothing.
- [x] `update_todos` then `get_latest_run` returns the list in order with each status; a status outside the four is 422.
- [x] `finish_run` is 409 naming the waiting claims while cap is left, and succeeds when the cap is used up or no claim is waiting.
- [x] `get_latest_run` is `null` before any run.
- [x] A test seeds a claim `in_progress`, restarts the app, and finds it `waiting` with one failure.
- [x] `create_run` is 409 while another run is unfinished.

## Phase 3: The claim agent as a subagent

The pilot's agent becomes the claim agent: a clean thread per claim, the manual by section, a one-line report, and failures recorded.

- `agent/claim.py`: `run_claim(claim_id, llm)` builds the agent on a fresh thread with `ModelRetryMiddleware` and the claim instructions, streams and logs steps as now, and returns the agent's one-line report.
- `work_claim(claim_id, run_id, llm)`: starts the claim against the run (reporting a refusal, such as the cap, in one line), runs `run_claim` inside the one `try/except Exception`; on an exception, or if the claim is still `in_progress` afterwards, calls `fail_claim` and returns `CLM-1004 failed: <error>. Back in the queue` (or `Left for a handler`).
- `backend/instructions/claim.md`: written from the policy wording and the manual (never from `data/evals/`): look up the contents, read the sections the claim needs (the benefit, the exclusions, section 10 of the wording; the manual's documents, payout, repeat claims, meals and interpretation sections as they apply), decide, record, pay, and reply in one line with the decision and payout.
- `run_one(claim_id)` creates a run with a cap of 1, calls `work_claim` (a second time if the first fails) and finishes the run; `run_agent` runs it in the background.
- `uv run -m agent CLM-1001` still runs one named claim.

**Success criteria**
- [x] `uv run pytest agent` passes on a stub model.
- [x] A stub that reads two sections, decides and pays leaves the claim `decided`, logs two `read` steps, and returns a one-line report.
- [x] A stub model that raises leaves the claim `waiting` with one failure and an error step; `work_claim` returns the failure line without raising.
- [x] A stub that stops without a decision is treated as a failure.
- [x] Two failing attempts leave the claim `needs_handler`.
- [x] A test proves `ModelRetryMiddleware` retries a model error once and the claim is still decided.
- [x] `GET /api/instructions/claim` returns the claim instructions; a test checks they mention the claims manual and not `data/evals`.

## Phase 4: The queue agent

One run works the queue, capped, with its to-do list, summary and missed-claims check.

- `agent/queue.py`: `run_queue(cap, llm)` creates the run, then a `create_agent` with the queue instructions, `ModelRetryMiddleware`, `ModelCallLimitMiddleware` as a backstop on runaway runs, and four tools bound to the run:
  - `list_waiting_claims`: the waiting claims in short form (id, type, amount, submitted date, failures);
  - `update_todos(todos)`: replaces the to-do list;
  - `work_claim(claim_id)`: the subagent from phase 3; returns its one line;
  - `finish_run(summary)`: ends the run, or returns the 409 naming missed claims.
- If the agent stops without finishing, the runner reminds it to check for missed claims and finish, at most twice, then closes the run with `stop_run` ("Stopped before finishing"). A crash closes the run the same way, so it never blocks the next run.
- `backend/instructions/queue.md`: list the waiting claims, choose an order and write the to-do list, work each claim with `work_claim` and tick it off, put failed claims back to do or mark them for a handler, check for missed claims, finish with a short summary for the handlers.
- `start_run` runs `run_queue` in the background. `uv run -m agent --cap 5` runs it from the command line.

**Success criteria**
- [x] `uv run pytest agent` passes on stub models (one for the queue agent, one for the claim agent).
- [x] Cap: a run capped at 2 on a scripted stub decides two claims; the other 23 stay `waiting`; a `work_claim` on a third claim returns the cap refusal.
- [x] A claim whose claim agent raises does not stop the run: the next claim is still decided and the run finishes.
- [x] The failed claim goes back to `waiting`, is worked again in the same run and decided.
- [x] A claim that fails twice is `needs_handler` and its to-do item is `for_handler`.
- [x] Missed claims: a stub that tries to finish early gets the refusal naming the waiting claim, works it, then finishes.
- [x] The run's to-do list and summary are stored and returned by `get_latest_run`.
- [x] `POST /api/runs/start` with `{cap: 1}` returns started and starts the queue agent with that cap; it is 409 while a run is going.
- [x] A queue agent that stops without finishing is reminded and then finishes; one that never finishes is stopped with "Stopped before finishing".

## Phase 5: The dashboard

The team can start a run, watch the to-do list, see what the agent read, and read both sets of instructions.

- Queue page: a "Run the queue" panel with a number input for the cap (default 5) and a start button, disabled while a run is going; the current run's to-do list with a badge per status (to do, in progress, done, for a handler) and the summary when it finishes; polled every three seconds with the queue.
- `needs_handler` badge, "Needs a handler", in Coral Red, and in the status bar.
- Claim page: "What the agent read", listing the `read` steps; the steps list labels them "Read".
- Instructions page: "Queue agent" and "Claim agent", one after the other, read only.
- "Waiting for sign-off" moves to Electric Blue, so Coral Red marks only the claims that need a handler.
- Brand from `docs/BRAND.md`, light and dark.

**Success criteria**
- [x] `npm run check` passes, including vitest tests for the run panel (no run, a run in progress, a finished run with its summary), the new badge, and the sections-read panel.
- [x] Playwright e2e (on its own backend, no model): the queue page shows the run panel; a to-do list written through `PUT /api/runs/{id}/todos` appears with its statuses; a claim with `read` steps shows them under "What the agent read"; the instructions page shows both agents' instructions.
- [x] Checked in the browser with the Playwright MCP server, light and dark: the run panel, a to-do list, the new badge and the claim page's read sections.

## Phase 6: A live run, and the docs

Proves it on the real model and brings the docs up to date.

- Reseed (delete `backend/undercover.db`), `npm run dev`, start a run capped at 5 from the dashboard.
- Update `docs/SPEC.md` (the manual moves out of "Not for now"; decisions follow the wording and the manual; queue runs, the to-do list and `needs_handler`), `docs/TECH_STACK.md` (two entry points, the default model, the middleware) and `docs/STATUS.md` (what's built, how to run it, the live run's results; the "stuck in progress" known limit removed).

**Success criteria**
- [x] After reseeding, a run capped at five on `deepseek/deepseek-v4.1-flash` works five claims on its own and finishes with a summary; the other 20 claims stay `waiting`.
- [x] In the browser with the Playwright MCP server: the to-do list fills in and is ticked off during the run; each worked claim shows its steps and the sections the agent read.
- [x] `docs/SPEC.md`, `docs/TECH_STACK.md` and `docs/STATUS.md` describe the product as it now is; `docs/PLAN.md` is unchanged.
- [x] `npm run check` is green.

## Not in this build

- Running claims in parallel: the queue agent works one claim at a time.
- Search over the documents: the contents and numbered sections are enough for two short documents.
- Rendering the instructions as formatted Markdown.
