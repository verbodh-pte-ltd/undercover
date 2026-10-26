# Status

The claims pilot from `docs/PLAN.md` and the harness from `docs/PLAN_HARNESS.md` are built: every phase is done and `npm run check` passes.

## What's built

- **Backend** (`backend/`): FastAPI on SQLite at `backend/undercover.db`, or the path in `UNDERCOVER_DB`. It is seeded on startup from the files in `data/` listed in `backend/seed.py`, including the claims manual, never from `data/evals/` or `data/security/`.
  - Reads (`reads.py`): `list_claims`, `list_waiting_claims`, `get_claim`, `get_policy`, `get_customer`, `list_plans`, `list_handlers`, `get_instructions` (for `queue` or `claim`).
  - Documents (`documents.py`): `list_documents` and `read_section`, the policy wording and the claims manual split at their numbered sections.
  - Writes (`actions.py`): `start_claim` (for a run, within its cap), `fail_claim`, `record_decision`, `pay_claim`, `sign_off_claim`, `add_step`, `list_steps`.
  - Runs (`run_routes.py`, `run_store.py`): `create_run`, `get_run`, `get_latest_run`, `update_todos`, `finish_run` (refused while a waiting claim is within the cap), `stop_run`, and starting agents in the background: `start_run` for the queue, `run_agent` for one claim.
  - The sign-off rule: an approval over $1,000 waits at `awaiting_signoff`, `pay_claim` returns 403 until a handler signs off, and the sign-off makes the payment.
  - Recovery: on startup, claims left `in_progress` count as a failed attempt and unfinished runs are closed.
- **Agent** (`agent/`), DeepSeek V4.1 Flash by default, in three modules: `backend.py` (the one client for the API, at `UNDERCOVER_API`), `tools.py` (every tool) and `harness.py` (the model, the middleware and the agents).
  - The queue agent (`run_queue`): lists the waiting claims, writes the to-do list and keeps it up to date, hands each claim to the claim agent with `work_claim`, and finishes with a summary. If it stops without finishing, middleware reminds it twice, then the run is stopped.
  - The claim agent: works one claim from a clean start, with the claim in its context so its tools can act on no other. It reads the sections of the wording and the manual it needs, decides and pays, and middleware logs every step on the claim. `work_claim` catches any failure, puts the claim back in the queue or leaves it for a handler, and reports back in one line.
  - `run_one` works one named claim in a run of its own, once; a failure puts it back in the queue.
  - Their instructions, `backend/instructions/queue.md` and `claim.md`, give each agent its goal and rules, written from the policy wording and the manual.
- **Dashboard** (`dashboard/`): the brand from `docs/BRAND.md`, light and dark.
  - `#/`: the run panel (start a run with a cap; the to-do list as a strip and a checklist; the summary), then the queue with a status bar and a badge for each claim, refreshed every three seconds.
  - `#/claims/<id>`: the claim in full; a button to run the agent on it; the decision, payout, reason and policy section; what the agent read; its steps, including failures; and the sign-off form.
  - `#/instructions`: the queue agent's and the claim agent's instructions, read only.
- **End-to-end tests** (`e2e/`): the queue, a claim page, the sign-off rule, the run panel and to-do list, the sections read and both instructions. They run on their own backend (port 8100, a fresh `e2e/test.db`) and dashboard (port 5174), so they never touch the dev data or call a model.

## Claim statuses

`waiting`, then `in_progress` while a claim agent works, then `decided`, or `awaiting_signoff` for an approval over $1,000 until a handler signs off. A claim that fails before its decision goes back to `waiting`; on its second failure it becomes `needs_handler`.

## Run it

- `npm run dev` starts the backend on port 8000 and the dashboard on http://localhost:5173.
- Run the queue from the queue page, or with `uv run -m agent --cap 5` while the backend is running. Run one claim from its page, or with `uv run -m agent CLM-1001`.
- The agents use `deepseek/deepseek-v4.1-flash` with low reasoning effort, or the model in `MODEL_NAME` such as `openrouter/free`. They need `OPENROUTER_API_KEY` in `.env`.
- Delete `backend/undercover.db` to start again from the seed data; do this before any live run.
- `npm run check` runs every check.

## Known limits

- One run at a time, and the queue agent works one claim at a time.
- The run's summary is written by the model. Its paid total is now right, but one run's summary miscounted the claims (it said "2 approved and paid" where one was paid). The claims and the to-do list are the record.
- Each claim agent reads 7 to 14 sections, most of the manual, so a claim costs more model input than it strictly needs. Goal-based instructions did not change this.
- The instructions page shows the Markdown as plain text.

## Live checks

After the review in `docs/REVIEW_HARNESS.md`, three reseeded runs capped at five claims on DeepSeek V4.1 Flash, each about two minutes, with no failures. All three reached the same five outcomes as the run below, and the other 20 claims stayed waiting. The first showed the to-do list ticked off only at the end. After the queue instructions said the handlers watch it live, the second and third ticked it off one claim at a time. The third (RUN-967bc379) summed up correctly: $420.00 paid, $3,390.00 waiting for sign-off.

Harness, after reseeding, a run capped at five claims on DeepSeek V4.1 Flash, started from the dashboard (RUN-0a050d67, about three minutes). The to-do list filled in and was ticked off one claim at a time; the other 20 claims stayed waiting; no failures.

- CLM-1001: approved for $420.00 under 3. Trip Delay, and paid.
- CLM-1008: approved for $2,240.00 under 2. Trip Cancellation and Interruption, waiting for sign-off.
- CLM-1010: declined under 9. General exclusions.
- CLM-1011: approved for $1,150.00 under 5. Baggage and Personal Effects (per-item limits, less the carrier's $350 and the $50 deductible), waiting for sign-off.
- CLM-1018: referred under 5. Baggage and Personal Effects.

Pilot:

- CLM-1001: approved for $420.00 under 3. Trip Delay, and paid.
- CLM-1003: approved for $3,750.00 ($3,800 less the $50 Premium deductible). The agent's own payment was blocked with 403, and sign-off in the dashboard paid it.
