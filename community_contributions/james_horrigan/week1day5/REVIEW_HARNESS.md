# Human review: the harness build

A review of the code written for `docs/HARNESS.md` (commits 89205a6 to c895aa6), done after a human reviewer said the agent implementation does not pass a simple review. Every finding below was checked against the code at c895aa6; the line numbers are from that commit.

The build was correct in what it did: every behavior the brief asked for worked and was tested, and `npm run check` was green. The problems are in how it was built. The agent was hard to read, spread over five modules with no clear seams, talked to the backend in three different ways, re-invented things LangChain already provides, and gave the model long scripts instead of goals.

## Findings

### 1. A hard-coded backend URL (confirmed, and a live bug)

`agent/tools.py:8` had `BACKEND_URL = "http://localhost:8000"`. The dashboard already reads the backend's address from `UNDERCOVER_API` (`dashboard/vite.config.ts:7`), but the agent ignored it.

The bug: the end-to-end backend runs on port 8100 with its own database (`playwright.config.ts`), and it starts agents in-process. Any agent it started would have read and written the **dev** backend on port 8000, not its own. The e2e tests never start an agent, so nothing caught it.

`agent/model.py` also hard-coded `OPENROUTER_BASE_URL`, which is acceptable for a fixed provider but should sit with the rest of the model's configuration.

### 2. Three ways to call the backend (confirmed)

There should be one. There were three:

- `tools.call(method, path, body)` returned the text, with errors turned into the string `"Error 409: ..."`.
- `runs.create_run`, `runs.latest_run` and `runs.get_claim` opened their own `tools.backend()` client and raised on errors.
- `claim.work_claim` used `tools.call` and then tested `started.startswith("Error")`, so control flow depended on parsing a string meant for the model.

Tests had to patch `tools.backend` to make all three work, and `test_queue.py` reached through `queue.tools.call` to read the instructions.

### 3. The harness was spread across five modules (confirmed)

- `model.py` held the model and the retry middleware.
- `claim.py` held the claim agent, step logging, failure handling, the one-line report and `run_one`.
- `queue.py` held the queue agent, its tools (as closures inside `queue_tools`), the reminder loop and `run_queue`.
- `runs.py` held backend calls.
- `tools.py` held the claim agent's tools and the HTTP client.

To follow one claim through a run you had to read four of them. The tools were in two places: the claim agent's in `tools.py`, and the queue agent's as nested functions in `queue.py`. The plan (`docs/PLAN_HARNESS.md`) chose this layout; it renamed the pilot's `main.py` to `claim.py` and added a module per agent instead of asking where the harness boundary was.

### 4. `run_queue` was unwieldy (confirmed)

`agent/queue.py:64-92`:
- built the middleware list with a type annotation to satisfy pyright;
- built the agent;
- ran a hand-written reminder loop around `agent.invoke`, re-sending the whole message list each time;
- called `runs.latest_run()` three times (by "latest", not by its own run id);
- stopped the run in a `finally`.

The reminder loop is exactly what LangChain middleware is for: an `after_model` hook can add a reminder and jump back to the model.

### 5. LangChain's own way to pass state was not used (confirmed)

- **Run state in closures.** The run id and the claim model reached the queue tools through closures (`queue_tools(run_id, claim_llm)`). LangChain's way is `context_schema` with `ToolRuntime`: the run's context is passed to `invoke(..., context=...)`, and each tool reads `runtime.context`.
- **The claim id came from the model.** The claim agent's tools took `claim_id` as an argument the model filled in, so a claim agent could decide or pay a different claim. The claim it is working belongs in its context.
- **Steps logged by re-parsing messages.** Steps were logged by streaming the agent and re-parsing every message (`describe`, `messages_in`, `section_read`). `read_section` prefixed its result with the document name only so that `section_read` could parse it back out. A `wrap_tool_call` hook sees the tool's name, arguments and result directly.
- **A thread id with no checkpointer.** `run_claim` passed a fresh `thread_id` for "a clean start". With no checkpointer that does nothing: every `invoke` already starts clean.

### 6. Both instructions were long, prescriptive scripts (confirmed)

- **Claim instructions.** `backend/instructions/claim.md` was an eight-step procedure that listed which tools to call in which order and, in step 3, nearly every section of the manual to read. That is why the live run read about ten sections a claim.
- **Queue instructions.** `backend/instructions/queue.md` was a seven-step procedure down to the wording of each to-do item. It told the agent how to retry claims and when to call `list_waiting_claims`, even though the backend already enforces the cap and the missed-claims check.

Scripts like these make the model a slow interpreter of a program we could have written in code. They also hide the goal and the judgement the model is there for. Instructions should say what the agent is for, what a good result looks like, the rules it must keep, and where to find things, then leave the steps to the model.

### 7. Other problems found in the agent

- **Business logic in the agent.** `queue.waiting_claims` filtered the queue to waiting claims and picked fields. The backend should answer "which claims are waiting".
- **A side effect hidden in a report.** `claim.outcome`, a function that formats a report, also recorded a failure through `failed()`.
- **An extra retry loop in `run_one`.** It retried a failed claim itself (`MAX_ATTEMPTS`), duplicating the queue's retry. One attempt is enough: a failed claim goes back to waiting.
- **`__main__.py` crashed with `IndexError`** when run with no arguments.

### 8. The same review applied to the backend

- **`Db` defined five times.** `Db = Annotated[sqlite3.Connection, Depends(get_db)]` was in five modules. It now lives once in `db.py`.
- **Three modules named for runs.** `runs.py`, `run_routes.py` and `run_store.py`. `runs.py` held two routes that start agents; it is merged into `run_routes.py`.
- **The not-found check repeated.** `runs.run_agent` repeated the claim lookup and 404 that `actions.claim_or_404` already does.
- **The backend imports the agent and runs it in-process**, while the agent calls the backend over HTTP. This is the pilot's design, and Week 3 moves the agent out to its own runtime, so it stays for now, but it is why the URL bug in finding 1 mattered.
- **A full-table read for one claim.** `run_store.fail_claim` and `missed_claims` load every claim's status to look up one. With 25 claims this is harmless, so it stays.

### 9. The same review applied to the dashboard

The dashboard passes. It has one API layer (`api.ts`) that every component goes through, no hard-coded backend address (Vite's proxy reads `UNDERCOVER_API`), and short components. Two small things:
- **A duplicated refresh constant.** `REFRESH_MS` was defined in both `Queue.tsx` and `RunPanel.tsx`.
- **A hard-coded cap limit.** The cap input was hard-coded to `max={25}`, today's number of claims, which will go stale as the queue grows.

## How this was allowed to happen

1. **The checks only test behavior.** `docs/PROCESS.md` defined done as success criteria that can be checked by running something: tests, commands, API calls, pages. Every criterion was met. Nothing in the process looks at the design, so working-but-messy code passed every gate, and the pre-commit hook passed it too.
2. **The code standards were too thin to catch it.** `docs/CODE_STANDARDS.md` had eight lines. It said nothing about configuration (no hard-coded URLs), one way to reach a service, separating the harness from the tools and the business rules, using the framework's own mechanisms, or how to write agent instructions.
3. **The plan fixed the module layout, and the build followed the plan.** `docs/PLAN_HARNESS.md` named `claim.py`, `queue.py` and the closure-bound queue tools. It also described both instructions as step lists. Nobody reviewed the design once the code existed, only the plan before it.
4. **The pilot's shortcuts were extended, not questioned.** The hard-coded URL, the `call`-returns-error-strings helper and stream re-parsing all came from the pilot. The harness build added to them rather than asking whether they still fit.
5. **No look at the framework first.** The build reached for closures and loops before checking what LangChain 1.x provides (`context_schema`, `ToolRuntime`, `wrap_tool_call`, `after_model` with `jump_to`).

## Recommendations

1. One backend client in the agent, with its address from `UNDERCOVER_API`, the same variable the dashboard uses.
2. Three agent modules with clear jobs:
   - `backend.py`: the one way to reach the API.
   - `tools.py`: every tool for both agents.
   - `harness.py`: the model, the middleware, the two agents and the entry points `run_queue` and `run_one`.
3. Run state through `context_schema` and `ToolRuntime`. The claim agent's tools take the claim from the context, not from the model.
4. Step logging and the reminder as middleware (`wrap_tool_call`, `after_model`). There is no stream parsing and no reminder loop.
5. Short, goal-based instructions for both agents.
6. Let the backend filter the queue (`GET /api/claims?status=waiting`) and return a run by id (`GET /api/runs/{run_id}`).
7. A design review step in `docs/PROCESS.md` before each phase is committed, and code standards that cover the points above.

## What was done

See the end of this file.

## What was done

Every recommendation above was carried out, in two commits, and checked with `npm run check` and live runs.

**The agent** is three modules instead of five:

| Module | Job |
|---|---|
| `agent/backend.py` | The one way to reach the API: `request`, plus `text` for tools (errors as text the model can read) and `data` for the harness (JSON, raising on errors). The address comes from `UNDERCOVER_API`. |
| `agent/tools.py` | Every tool for both agents, the `ClaimContext` and `RunContext` they read through `ToolRuntime`, and `work`, the handoff to the claim agent with its one broad `except`. |
| `agent/harness.py` | The model, the middleware (`ModelRetryMiddleware`, `ModelCallLimitMiddleware`, a `StepLog` class for the claim agent's steps, and an `after_model` reminder that jumps back to the model), both agents, `run_queue` and `run_one`. |

- **Simpler entry points.** `run_queue` is now ten lines with no loop. `run_one` tries a claim once; a failure puts it back in the queue like any other.
- **The claim comes from the context.** The claim agent's `get_claim`, `record_decision` and `pay_claim` take the claim from the context, so a claim agent can only act on its own claim (`test_claim_tools_act_only_on_the_agents_claim`).
- **No string parsing.** Steps come from `wrap_tool_call` and `after_model`, not from re-parsing streamed messages, so `read_section` returns the section as it is.
- **Removed:** `agent/claim.py`, `queue.py`, `runs.py` and `model.py`.

**The instructions** now give each agent its goal, what good looks like, the rules and where to find things, in about half the words.
- **The to-do list in the first live run.** The queue agent ticked the list off only at the end, so the instructions now say why the list matters: the handlers watch it live.
- **The referral in the second run.** The queue agent marked a referred claim "for a handler", so the instructions now say a decision of any kind is "done".

**The backend:**
- `Db` is defined once in `db.py`.
- `runs.py` is merged into `run_routes.py`, and `run_agent` uses `reads.found` for its 404.
- There are two new operations: `list_waiting_claims` (`GET /api/claims/waiting`, its own operation because `docs/TECH_STACK.md` allows no optional inputs) and `get_run` (`GET /api/runs/{run_id}`).
- The e2e backend now gets `UNDERCOVER_API` too, so an agent started there would use its own backend.

**The dashboard:** `REFRESH_MS` is shared from `hooks.ts`, and the cap input has no hard-coded maximum.

**Checks:** `npm run check` is green: ruff, pyright, 59 pytest tests at 98% coverage, ESLint, Prettier, tsc, 16 vitest tests and 7 Playwright tests. Three live runs on DeepSeek V4.1 Flash, each reseeded and capped at five, decided the same five claims the same way as before the rework. The third run's summary gave the amount paid correctly: $420.00 paid, $3,390.00 waiting for sign-off.

**So it doesn't happen again:**
- `docs/CODE_STANDARDS.md` now has rules on the points above: addresses from the environment, one client per service, the layers kept apart, the framework first, state through the context, typing and instructions.
- `docs/PROCESS.md` now has a review of each phase's diff against the standards before it is committed, and a review of the whole build at the end. It also requires that the plan sets out the module layout and checks it against the standards, that earlier shortcuts are fixed rather than built on, and that instruction changes are checked with a live run.

**Still open:**
- Claim agents read 7 to 14 sections a claim; goal-based instructions did not reduce this.
- The run summary is still written by the model and can miscount.
- The backend still runs the agent in-process, until Week 3 moves it out.
