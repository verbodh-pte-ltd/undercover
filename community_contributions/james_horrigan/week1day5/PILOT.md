# Pilot progress

## 1. Reviewing the docs before planning

Before planning the pilot, I read the documents in `docs/` and made small changes to make the build my own. I kept the changes minimal so the spec and the course still line up.

| File | Change | Why |
|---|---|---|
| `docs/BRAND.md` | New palette: Electric Blue, Cyan and Lavender as the three main colors, Mint for information and Coral Red for alerts, with new ink and surface colors. Headings use Archivo, body stays Instrument Sans. | To give the dashboard a distinct look from the reference build. |
| `docs/CODE_STANDARDS.md` | Added two rules: short modules and functions with clear names, and a one-line docstring on every public function. | To keep the code easy to read and review as it grows. |
| `docs/PROCESS.md` | Added a "Fixing bugs" section: reproduce, prove the cause, fix the root cause with a test, and stop after two failed attempts. | To stop the agent guessing or papering over bugs, and to make it say when it's stuck. |

`docs/SPEC.md`, `docs/TECH_STACK.md` and `docs/STATUS.md` are unchanged.

## 2. Planning the pilot

Asked the agent to "plan the pilot". It wrote `docs/PLAN.md`: six phases covering backend data, decisions and the $1,000 sign-off rule, the agent, then the dashboard and end-to-end tests. Key choices: the backend enforces the payment limit rather than trusting the agent, the agent reaches data only through the API so it can't see `data/evals/`, and the runner logs the agent's steps for the dashboard.

## 3. Building the pilot

Asked the agent to "implement the plan for the pilot". It built the six phases in order, checked each one's success criteria, and committed after each phase.

- **Backend:** SQLite seeded from `data/`, with a test proving it never reads `data/evals/`. The $1,000 rule lives in the payment operation, so the agent can't pay a big claim without a handler.
- **Agent:** tools that only call the API, and a runner that logs every step for the dashboard.
- **Dashboard:** the queue, the claim page with live steps and sign-off, and the instructions, all in my brand colors.
- **Tests:** 27 Python, 10 dashboard and 4 end-to-end, with no model calls. `npm run check` passes.

Live runs with a real model:

- CLM-1001: approved for $420.00 and paid.
- CLM-1003: approved for $3,750.00. The agent tried to pay and was blocked, and paying it took a handler's sign-off in the dashboard.

Lessons from the build:

- The first live run showed the model guessing ids, so the instructions now tell it to read the claim first.
- Raw tool results made the steps hard to read, so they're cut to two lines.

## 4. Planning the harness

Copied the Week 1 Day 5 brief into `docs/HARNESS.md` and asked the agent to plan it. It wrote `docs/PLAN_HARNESS.md`: six phases covering documents by section with the new default model (DeepSeek V4.1 Flash), runs and recovery in the backend, the claim agent as a subagent, the queue agent, the dashboard, then a live run and the docs. Key choices:

- **Two agents, no new framework.** A queue agent plans the run and hands each claim to a claim agent that starts clean and reports back in one line, so the queue agent's context grows by one line per claim.
- **The backend owns the guarantees, the agent owns the plan.** The cap, no double payments, retries (back to `waiting`, then `needs_handler` on a second failure) and the missed-claims check are enforced in the backend, where tests can prove them.
- **One place catches failures.** `work_claim` wraps the claim agent in a single `try/except`, so one bad claim never stops the run.
- **Documents by section.** The wording and the claims manual are split at their numbered headings, and the agent reads only the sections it needs.

## 5. Building the harness

Asked the agent to implement the plan. It built the six phases in order and committed after each one.

- **Backend:** runs with a cap, a to-do list per run, `fail_claim` with retry and `needs_handler`, `finish_run` refused while a waiting claim is within the cap, and startup recovery for claims left `in_progress`.
- **Agent:** the queue agent with `list_waiting_claims`, `update_todos`, `work_claim` and `finish_run`, and the claim agent reading sections of the wording and the manual.
- **Dashboard:** a run panel with a cap and the live to-do list, a Coral Red "Needs a handler" badge, "What the agent read" on the claim page, and both agents' instructions.

Live run capped at five on DeepSeek V4.1 Flash (RUN-0a050d67): five claims worked with no failures, the other 20 stayed waiting, and the to-do list ticked off one claim at a time.

## 6. Reviewing the harness

Every behavior worked and `npm run check` was green, but the agent code didn't pass a simple human review. I had the agent write the findings to `docs/REVIEW_HARNESS.md`. The main ones:

- **A live bug:** the agent hard-coded `http://localhost:8000`, so an agent started by the e2e backend would have written to the dev database.
- **Three ways to call the backend**, including control flow that parsed error strings meant for the model.
- **The harness spread across five modules**, with tools in two places.
- **LangChain's own features not used:** run state passed through closures instead of `context_schema` and `ToolRuntime`, the claim id filled in by the model, steps logged by re-parsing streamed messages, and a hand-written reminder loop instead of middleware.
- **Instructions written as long step-by-step scripts**, rather than goals.

Why it happened: the process only checked behavior, the code standards were too thin to catch design problems, the plan fixed the module layout, and the pilot's shortcuts were built on instead of questioned.

## 7. Updating the harness

The agent carried out every recommendation from the review:

- **Three agent modules:** `backend.py` (the one API client, its address from `UNDERCOVER_API`), `tools.py` (every tool) and `harness.py` (the model, middleware, both agents and the entry points).
- **State through the context**, so a claim agent can only act on its own claim.
- **Middleware** for step logging (`wrap_tool_call`) and the finish reminder (`after_model`), with no stream parsing and no loop.
- **Short, goal-based instructions** for both agents.
- **Backend:** one `Db` dependency, run routes in one module, and new `list_waiting_claims` and `get_run` operations.
- **To stop it happening again:** `docs/CODE_STANDARDS.md` gained rules on configuration, one client per service, layering, using the framework first and writing instructions, and `docs/PROCESS.md` now reviews each phase's diff against the standards before it's committed.

Checks: `npm run check` green with 59 pytest (98% coverage), 16 vitest and 7 Playwright tests. Three reseeded live runs capped at five reached the same five outcomes as before the rework. Two instruction fixes came from those runs: the to-do list is ticked off as the agent goes because handlers watch it live, and a referred claim counts as "done".

Still open:

- Claim agents read 7 to 14 sections per claim; goal-based instructions didn't reduce this.
- The run summary is written by the model and can miscount.
- The backend still runs the agent in-process, until Week 3 moves it out.
