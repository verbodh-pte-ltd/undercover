# Status

The claims pilot (`docs/PLAN.md`), the agent's harness (`docs/PLAN_HARNESS.md`), durable state
(`docs/PLAN_DURABLE.md`, from `docs/DURABLE_LOCAL.md`) and the swarm and scoreboard
(`docs/PLAN_SWARM.md`, from `docs/SWARM.md`) are built, and of the improver (`docs/PLAN_IMPROVE.md`,
from `docs/IMPROVE.md`), phase 1: handlers sign in, phase 2: settings and the Settings page, phase 3: the
configuration in versions, phase 4: the judge, phase 5: rounds in the backend, phase 6: the
improver, phase 7: the Improvement page and the look of the dashboard, and phase 8: the live runs and
the documents, with the designer's review of the screenshots and its fixes. We use Postgres in Docker on this machine, not
Supabase, and Supabase Auth from Supabase's local stack for the sign-in.

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

In the improver build's live runs on local Postgres with GPT-6 Luna and local Supabase Auth, after one
reseed, all driven through the dashboard in headless Chromium:

- Round 1, signed in as Dana Whitfield: the first evaluations (1, 2 and 3 of 3) took 73 seconds. The
  improver's first proposal (a Trip Delay evidence rule and a broad unattended-property exclusion)
  was rejected as too broad; it came back within 14 seconds with the exclusion tied to the wording's
  own conditions, which was approved. Version 2 was evaluated (1, 2 and 3 of 3) and kept: held-out
  accuracy 78% to 94%, grade 1.28 to 1.77, cost per claim $0.00123 to $0.00130 (+6%, within the 100%
  tolerance), time per claim 15.0 to 14.9 seconds. Its recipe was approved. The round cost $0.199 of
  its $0.50 limit.
- Round 2: no first evaluations, since version 2 had its three. The improver proposed a rule for
  repeat claims in the customer's history (version 3), approved; it was rolled back because no
  measure improved by more than its noise: accuracy 94% to 94%, grade 1.77 to 1.75, cost -2.5%, time
  +16% (within the 25% tolerance). Its recipe was approved. The round cost $0.102.
- Version 1's two held-out runs differed by 0.04 in accuracy (76% and 80%; noise 0.10, tolerance
  0.10), 0.098 in grade (1.23 and 1.33; noise 0.15, tolerance 0.15), 0.6% in cost per claim (noise
  15%, tolerance 100%) and 7.1% in time per claim (15.6 and 14.5 seconds; noise 15%, tolerance 25%):
  all within the noise. On the evaluation set version 1 scored 88%, version 2 80% and version 3 96%.
- Through Langfuse's API, round 1's improver work is 4 traces in the session `Round 1` and round 2's
  3 traces in `Round 2`, one for each stretch of its work.
- A dispatch at N=25 on version 2: 14 approved, 4 declined, 4 referred and 3 payouts waiting for
  sign-off (CLM-1003 $3,750, CLM-1008 $2,240, CLM-1011 $1,150), $0.032 of model calls, 27 seconds.
  Signed in as Marcus Oyelaran, CLM-1003 shows a note in place of the "Sign off" button and the
  Settings page is read only (13 fields disabled, no save), and with his own token the backend answers
  403 to the sign-off and to a settings change. Signed in as Dana, the same claim was signed off and
  paid $3,750 within 5 seconds.
- What the live checks spent, from the costs recorded in the database: $0.289 for the rounds' claim
  agents (9 evaluations, 225 claims), $0.032 for the dispatch, $0.005 for the improver (11 calls) and
  $0.007 for the judge (225 grades), $0.333 in all.

A product designer reviewed screenshots of every page in light and dark mode against `docs/BRAND.md`.
What changed for it, in the look only:

- Diffs (versions, recipes and proposals) compare lines with their spacing evened out, so a line only
  indented again isn't a change, and show two unchanged lines around each change with "…" between changes.
- The Configuration page puts each version's reason on its own line under the version, and the changes
  columns there and on the Improvement page have a "Changes" heading.
- The Queue shows a dispatch's summary and sign-offs first, with its claims behind "Show all N claims".
- Settings show dollars as $1,000.00 until an input is edited, line the noise and tolerance headings up
  with their inputs, say the models are set by IT, and color the radio buttons Cerulean.
- The Scoreboard has a Version column and the headings "Decision right" and "Decision and payout right";
  an evaluation shows its version, names each copy by the claim it copies and its grades as "A, 2.07 of 3".
- The Improvement page keeps dates on one line and reasons at one size; the claim page shows dates as
  "Jun 27, 2026" and folds each tool result to one line that opens on a click; links in dark mode are a
  lighter Cerulean, 5.6:1 on the page.

## The parts

- `backend/`: FastAPI with SQLAlchemy Core. With `DATABASE_URL` set (as `npm run dev` does from `.env`)
  it uses local Postgres in Docker, in the `undercover` schema, with a pool of 4 connections that
  callers wait for. Without it, it uses SQLite in `backend/undercover.db` (or the file in
  `UNDERCOVER_DB`), as the tests do. `backend/store.py` seeds the database from `data/` on first start:
  the 25 claims, the policies, the customers, the held-out claims, the handlers from
  `data/handlers.json` (with their emails, and whether each can sign off over the limit; only Dana
  Whitfield can), the handlers' decisions on both labelled sets in `answer_key`, with each
  handler's reason and reference from `data/evals/`, which no claim operation reads, and version 1 of the claim agent's configuration and of the improver's recipe. `npm run seed` rebuilds every table,
  empties the agent's saved threads and seeds again, so it empties the scoreboard too.
  - Read routes for claims, policies, claim history, the policy wording, the handlers, and the policy
    wording and the claims manual by numbered section (`/api/documents/{document}/sections`).
  - Write routes: start a claim in a run or none, record steps, record a decision, pay, sign off,
    release a claim that failed, and record a model call's tokens and cost or a stretch of work.
  - Runs are dispatches or evaluations: `create_run`, `list_run_claims` (claims in progress first, then
    waiting claims oldest submitted first, or an evaluation's copies), `finish_run`,
    `stop_running_runs` and `get_current_run` (the latest dispatch, with its claims, summary, model cost
    and time). Only one runs at a time. Each run records the configuration version it works to
    (`version`, 404 for one that doesn't exist), and `get_run_configuration` gives a run's
    instructions, model and effort.
  - Sign-in: `backend/auth.py` checks a Supabase access token against the public keys at
    `{VITE_SUPABASE_URL}/auth/v1/.well-known/jwks.json` with PyJWT's `PyJWKClient`, audience
    `authenticated`, and the backend finds the handler by the token's email. `start_dispatch`,
    `start_evaluation`, `sign_off`, `change_settings` and `list_handlers` (which leaves out the
    emails) need a signed-in handler (401 without one); `GET /api/handlers/me`
    (`get_signed_in_handler`) returns the handler. The agent's own calls need no sign-in.
  - Settings live in the `settings` table, seeded from `data/company.json` (the approval limit,
    $1,000) and `data/improvement.json` (the objectives, the noise, the tolerance, the spending
    limit for each round, $0.50, and 25 claim agents at once). `GET /api/settings` (`get_settings`)
    returns them with the models allowed (`data/models.json`, or `MODEL_NAME` alone at effort
    `default` and price 0 when it is set) and the latest change to each. `PUT /api/settings`
    (`change_settings`) takes every setting, checks each value, records who changed which and when
    in `setting_changes`, and answers 403 for a handler who can't sign off over the limit.
  - `start_dispatch` and `start_evaluation` ask the agent's service through `backend/agent_service.py`,
    and pass on its refusal (409) or say it isn't running (503).
  - An evaluation works copies of its set's claims, with ids such as `E3-CLM-1001`, out of the queue.
    Nobody signs off a copy. `list_evaluations` and `get_evaluation` score each one against
    `answer_key`: decision accuracy, accuracy (the payout within a dollar), the cost of mistakes, the
    straight-through rate, model cost and time per claim, and the time for the run.
  - The judge (`backend/judge.py`) grades each claim of an evaluation as `finish_run` finishes it:
    Jev (`typesafe/jev-1.13`) through OpenRouter's Decisions endpoint, with the lab's rubric and three
    questions, comparing the agent's decision, reason and section with the handler's decision, reason
    and reference. The calls run in parallel, each with a 10-second timeout and the SDK's retries off,
    at the address in `JUDGE_URL` (`https://openrouter.ai` if unset), read once. Each claim gets a
    grade from 0 to 3 (Jev's score), a letter F, C, B or A (the most likely level, never the score
    rounded) and the two checks, same grounds and same section, kept as probabilities in `grades`
    with the judge's cost. A claim the agent didn't decide gets an F without a call; a claim the judge
    couldn't grade has no grade and is left out of the average. An evaluation's grade is the average
    (none if no claim was graded) and its `judge_cost` is kept apart from the claim agent's cost. With
    `MODEL_NAME` set the judge isn't called and every claim shows as not graded. A version's held-out
    scores include the grade. `finish_run` finishes only a run still running, and only that call
    grades it, so a second call pays the judge nothing and a stopped run isn't graded.
  - Rounds of the improver (`rounds`, with each approval or rejection in `round_events`). A handler who
    can sign off over the limit starts (`POST /api/rounds`), stops (`POST /api/rounds/{id}/stop`),
    approves (`/approve`) or rejects (`/reject`, with a reason) one; anyone else gets 403 saying why.
    Only one round is open at a time. A start makes the round, with a copy of the settings, the recipe
    version, the current version and a new thread id, and asks the agent's service (`POST /rounds`);
    a refusal passes on and leaves no round. An approval or rejection is passed to the service
    (`POST /rounds/{id}/decision`) with the handler, and the backend records nothing of its own.
    `GET /api/rounds` and `GET /api/rounds/{id}` show each round: its stage, the proposal waiting with
    its diff, the version tried, the verdict and why, the held-out scores before and after, who approved
    or rejected what, its runs, what it spent and what's left, and its `progress` in the first
    evaluations or the evaluations: how many it has started of those the stage needs (the first
    evaluations need only what earlier rounds left the current version short of). The service records a round through open
    routes: its stage, a proposed change (422 for a model or effort outside the models allowed) or
    recipe, the handler's decision (an approved change becomes a version under test, an approved recipe
    the next recipe), the improver's own model calls, and its whole-set evaluations
    (`POST /api/rounds/{id}/evaluations`, on the round's versions only, with the round's agents at once,
    on the version's own model and effort, or `MODEL_NAME` at `default`).
    `POST /api/rounds/{id}/verdict` applies the rule in `backend/rule.py` (the lab's): kept when the
    version meets the objectives, a measure improves by more than its noise and none gets worse by more
    than its tolerance, with accuracy and the grade as differences and cost and time as fractions of the
    old value. A version with no grade, with a stopped evaluation, or without its three evaluations is
    rolled back. `GET /api/rounds/{id}/results` is the improver's view: the current version and its
    scores, the wrong claims and five weakest right ones of its latest evaluation-set run beside the
    handlers' reasons and the judge, the earlier rounds, the objectives, noise, tolerance and rule,
    what's left of the limit and the models allowed, and nothing from the held-out set but its scores.
  - A round's spending is the improver's calls, its evaluations' calls and the judge. An evaluation is
    refused when its estimate (its claims times the highest cost per claim seen on its model, or the
    current model's scaled by the larger price ratio) is more than what's left. Once the limit is
    spent, or the round is stopped, `start_claim` refuses the next claim of its evaluations and marks
    the run stopped. A stopped round refuses everything it records afterwards and rolls back a version
    not yet judged, so the current version stays as it was.
  - A claim that fails before its decision goes back to `waiting`; after two failures it is
    `needs_handler`. A claim stopped `in_progress` stays there for the next dispatch.
  - `POST /api/payouts` (`work_out_payout`) applies the payout rules in `backend/payout.py`.
  - A payout over the approval limit is refused until a handler signs off. Whether a claim needs a
    sign-off is kept with its decision (`needs_sign_off`), from the limit at that moment, so a new
    limit applies only to the decisions recorded after it. A sign-off records the signed-in handler and when (403 for a handler who can't sign
    off over the limit), puts the claim back `in_progress`, and asks the agent's
    service to carry on and pay it.
  - The same decision or payment sent again counts as done; a different decision is still refused.
  - The claim agent's configuration (its instructions, model and reasoning effort) is kept in
    versions in `config_versions`, each with its reason, who proposed and approved it and when, and
    whether it was kept (empty while under test). The current version is the newest one kept.
    `GET /api/configuration/current` (`get_current_configuration`) gives it with no scores.
    `GET /api/configuration/versions` (`list_versions`) lists every version newest first, with its
    held-out scores (the averages of the whole held-out evaluations rounds ran on it; none until the
    improver runs rounds) and, from `difflib`, the lines it changed from the version before. The
    improver's recipe is kept in versions in `recipes`; `GET /api/recipes` (`list_recipes`) lists
    them newest first with the lines each changed. When the agent's service starts, it stops any round
    at work (`POST /api/rounds/stop-working`); a round waiting for a handler stays open.
- `backend/instructions/claim.md` and `backend/instructions/recipe.md`: version 1 of the claim agent's
  instructions (on `openai/gpt-6-luna` at low effort) and of the improver's recipe (the lab's first),
  which seeding copies into the database.
- `agent/`: the UnderCover Agent, in six parts (`docs/CODE_STANDARDS.md`).
  - The claim agent is a LangGraph graph (`claim_graph` in `agent/main.py`) with seven tools and no pay
    or sign-off tool. It works to its run's configuration version (the current one for a claim in no
    run): its instructions, model and reasoning effort, or `MODEL_NAME` at the model's default effort. At
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
  - The agent's service (`agent/service.py`, port 8002) starts dispatches and evaluations on the
    current version, recording its model and effort (`default` with `MODEL_NAME`), refusing a
    second while one runs, resumes signed-off claims, and marks any run still running as stopped when
    it starts.
  - The improver (`agent/improver.py`) is a LangChain `create_agent` on `openai/gpt-6-luna` at medium
    effort (or `MODEL_NAME`), with the lab's four tools (`read_results`, `propose_change`,
    `run_evaluation`, `propose_recipe`), which reach the backend only through `agent/improver_api.py`.
    Its instructions wrap the round's recipe. `HumanInTheLoopMiddleware` pauses it on each proposal,
    saved by a `PostgresSaver` of its own (in memory without `DATABASE_URL`) on the round's own thread.
    Parallel tool calls are off, at most 30 model calls each stretch of work, and each call's cost is
    recorded with its round. A proposal the backend refuses (a model not allowed) goes back to it as a
    rejection without reaching a handler.
  - A round runs in the agent's service (`POST /rounds`, `POST /rounds/{id}/decision`): the first
    evaluations the current version lacks (1 on the evaluation set, 2 on the held-out), then the
    improver until it pauses. A handler's answer is recorded with the backend, then the improver
    carries on; after an approved change `run_evaluation` runs the version once and twice through the
    dispatcher, in a fresh `contextvars.Context`, and asks for the verdict. Each stretch holds the
    one-run lock and lets it go at a pause; a start or an answer while something else runs is refused,
    naming it. A stopped round makes no more model calls; a round that fails is stopped.
  - Each claim is traced in Langfuse in a session named after its thread, tagged `run-<run_id>`, and
    `round-<round_id>` in a round's evaluations. The improver's work is in one session, `Round <id>`.
  - `uv run -m agent CLM-1001` works named claims with no run.
- `dashboard/`: the claims desk. Every page needs a signed-in handler: the sign-in page signs in
  with `@supabase/supabase-js` `signInWithPassword` (no sign-up), and the header shows who is signed
  in with a "Sign out" button. Each action sends the session's access token. Every page has the
  same header (its title and what it is for), the navigation marks the page you are on, numbers are
  right-aligned with their units, statuses are a colored chip with a word, and a page that can't load
  says to check the backend and reload. Handler ids show as names, and version 1's as "Seeded".
  - The queue (`#/`) has a count per state, a "Claim agents at once" picker (1 to 25, 3 to start) and a
    "Start dispatch" button, and the latest dispatch: its claims and where each stands, its model cost
    and time, and its summary once it has finished.
  - The Scoreboard (`#/scoreboard`) starts an evaluation (set, whole or tiny, N) and lists evaluations
    newest first with every measure, the average grade among them. `#/scoreboard/<run_id>` shows its
    claims beside the handlers', with the misses marked, each claim's grade and letter and the two
    checks as yes or no, the judge's cost, and each claim linked to its page.
  - Each claim (`#/claims/CLM-1001`) shows its decision, reasons, the agent's steps, the sections it
    read, the policy and earlier claims. A claim waiting for sign-off has a "Sign off" button, except on
    an evaluation's copy; a handler who can't sign off over the limit sees a note saying so instead.
  - The Configuration page (`#/configuration`; `#/instructions` goes there too) shows the current
    version with its instructions, and every version with its status, reason, who proposed and
    approved it, and its held-out scores. Each version after the first opens what it changed from
    the one before: the model and effort before and after, and the changed lines in color.
  - The Improvement page (`#/improvement`) shows the latest round: who started it, what it has spent
    of its limit, its stages as they happen (the first evaluations and the evaluations with "k of n"),
    the verdict and why, and who approved or rejected what. Dana starts or stops a round after a
    confirmation in the dashboard's own dialog. A proposal waiting shows the model and effort before and
    after, the reason and the diff (the same `Diff` component as the Configuration page), with Approve
    (after a confirmation) and Reject (a reason required). Marcus and Priya see it all, with a note
    saying why they can't act. Below are two SVG charts of each scored version's held-out accuracy and
    grade (filled when kept), the rounds newest first with the scores before and after, the verdict
    and the cost, and the recipe's versions with their diffs. It refreshes every 3 seconds.
  - The Settings page (`#/settings`) shows every setting with who last changed it, and the models
    allowed with their reasoning efforts and prices. Dana can change the settings, after a
    confirmation in the dashboard's own dialog; Marcus and Priya see them read only, with a note
    saying why. It also sets the appearance (light, dark or the computer's), kept in the browser
    under `undercover-theme` and applied through `data-theme` on the page.
- `e2e/`: Playwright tests, one at a time, on their own servers (backend :8001, dashboard :5174) and
  their own SQLite database, `e2e/e2e.db`, deleted at the start of each run. A stand-in for the agent's
  service on :8003 (`e2e/agent-stand-in.mjs`) records the backend's calls with their bodies. A
  stand-in for Supabase Auth on :8004 (`e2e/auth-stand-in.mjs`) signs tokens with its own key,
  serves its public key and answers CORS preflights; every spec signs in first. A stand-in for the
  judge's endpoint on :8005 (`e2e/judge-stand-in.mjs`) gives every claim a B at 2.2.
  `e2e/rounds.spec.ts` starts a round on the dashboard, records a proposal through the backend's
  routes as the agent's service would, approves it on the page, checks the approval reached the
  stand-in with the handler, records it, and stops the round. The specs pass in any order.

## Run it

```bash
npm run dev      # backend on :8000, dashboard on :5173, agent service on :8002
```

Start a dispatch from the queue page and an evaluation from the Scoreboard page. To work named claims
without a run:

```bash
uv run --env-file .env -m agent CLM-1001
```

Sign in as a handler from `data/handlers.json` with `HANDLER_PASSWORD` from `.env`. Supabase's local
stack must be running (`npx supabase start`, as in `labs/week2/week2day5.ipynb`), with
`VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` in `.env`; Vite reads `.env` from the repo
root for `npm run dev` only.

The `undercover-postgres` container must be running. To reset the data, run `npm run seed`; the start
cell in `labs/week2/week2day3.ipynb` makes the container again from `.env` if Docker has lost it.

## Check it

```bash
npm run check    # Python checks, TypeScript checks, then the end-to-end tests
```

`npm run check` needs no `.env`: the tests run on SQLite, with no Postgres, Langfuse or OpenRouter.

## Not built yet

- Rejecting a held payout. A handler can only sign off for now.
- Real payments and messages to customers, as `docs/SPEC.md` says.
