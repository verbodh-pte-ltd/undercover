# Plan: an improver for the claim agent, and handlers who sign in

The brief is `docs/IMPROVE.md`. The claim agent's configuration goes into versions, a judge grades each
explanation in an evaluation, and an improver agent proposes new versions and new recipes that a
handler approves on the dashboard. Handlers sign in, the platform's settings get a page of their own,
and the whole dashboard is brought up to the standard of enterprise software.

SIDENOTE: For consistency with the course, please read `labs/week2/week2day5.ipynb` and build the judge,
the configuration's versions and the improver in the same style as the lab. Where the lab and this brief
differ, follow the brief.

DATABASE NOTE: As in `docs/DURABLE_LOCAL.md` and `docs/PLAN_SWARM.md`, we use Postgres running locally in
Docker, not Supabase. The database is the `undercover-postgres` container (Postgres 17) on
`127.0.0.1:5432`. Sign-in uses Supabase Auth from Supabase's own stack on this machine (`supabase/config.toml`,
started by the Week 2 Day 5 lab), on `127.0.0.1:54321`, with no Supabase account (`docs/LOCAL_LABS.md`
section 4). Wherever the brief says Supabase, read local Postgres for the data and the local stack for
sign-in.

## Choices for you

Review these first. The documents in `docs/` don't make these decisions.

1. **The brief's notes follow Ed's build, not ours.** Ours has no `agent/api.py`: the claim agent reaches
   the backend through `backend()` in `agent/main.py`. The handlers' decisions are in `answer_key`, not
   `handler_decisions`. There is no `handlers` table and no `SignOffForm.tsx` (ours is `SignOff.tsx`),
   and `sign_off` (not `sign_off_claim`) takes a `handler_id`. There is no
   `test_the_agent_cant_read_the_scoreboard`. I follow the intent: the improver's calls go in a new
   `agent/improver_api.py`, and a new test checks that nothing the claim agent calls reaches the
   scores, `answer_key` or the rounds.
2. **A `handlers` table**, seeded from `data/handlers.json` with the email and `can_approve_over_limit`.
   The emails are already in the file. Marcus Oyelaran and Priya Raman get `false`.
3. **Two new files in `data/`:** `data/improvement.json` (objectives, noise, tolerance, the round's spending
   limit of $0.50 and 25 agents at once, the lab's values) and `data/models.json` (the lab's three models
   with their reasoning efforts and prices as numbers, in dollars per million input and output tokens).
   The approval limit still seeds from `data/company.json`.
4. **The recipe file** is `backend/instructions/recipe.md`, beside `claim.md`, with the lab's first recipe.
5. **The approval limit is kept on each decision.** `record_decision` stores whether the claim needs a
   sign-off (`needs_sign_off`) from the limit at that moment. `pay_claim` checks that flag, not the
   current limit, so a new limit applies only to decisions recorded after it.
6. **New tables:** `settings` (one row per setting, as JSON) with `setting_changes` (who, what, when);
   `config_versions` and `recipes` (the text, the reason, who proposed and approved it, and when);
   `rounds` (stage, recipe version, the settings as they stood at the start, the version tried, the
   proposal waiting, the reason, the scores before and after, the verdict and why, the thread id, who
   started or stopped it); `round_events` (each approval or rejection, with the handler, the reason and
   the time); `grades` (per evaluation claim: grade, letter, the two checks and the judge's cost).
   `runs` gains `version` and `round_id`. `model_calls` gains `round_id`, and its `claim_id` may be empty
   for the improver's own calls. `answer_key` gains `reason` and `reference`.
7. **A round runs in the agent's service**, in a thread that holds the one-run lock while it works: the
   first evaluations, the improver up to a pause, and the evaluations after an approval. The lock is let go
   at each pause, so dispatches and evaluations can run while a round waits for a handler.
8. **A restart:** when the service starts, a round in a working stage is marked stopped, and a round
   waiting for approval stays open and resumes from its `PostgresSaver` checkpoint when a handler answers.
9. **The spending limit is checked before each claim.** The dispatcher's worker asks the backend what the
   round has spent (the improver's calls, the evaluations' calls and the judge so far) and starts no new
   claim once it reaches the limit. The judge's cost for an evaluation lands when it finishes, so a round
   can end a few hundredths of a cent over.
10. **The improver's call cap** is LangChain's `ModelCallLimitMiddleware` with a run limit of 30, matching
    the claim agent's 30. `parallel_tool_calls=False` goes into the model settings through a small
    middleware, as the brief asks.
11. **Who proposed a version** is recorded as `improver (round N)`, or `seed` for version 1.
12. **Navigation:** Queue, Scoreboard, Improvement, Configuration and Settings. `#/instructions` goes to
    the Configuration page. The appearance is set through `data-theme` on the page, remembered under one
    key in `localStorage`, and following the computer by default.
13. **The browser checks** use the Playwright MCP server. It isn't connected in this session, so the backup
    is the Chrome DevTools MCP server.
14. **Eight phases**, not 5 or 6, so that each one keeps to 8 success criteria.

## Phase 1: Handlers sign in

The backend checks a Supabase token on every handler action, and the dashboard signs in before it shows a
page. A sign-off records the signed-in handler.

- [x] `handlers` table seeded from `data/handlers.json` with emails, Marcus and Priya at
      `can_approve_over_limit: false`; `GET /api/handlers/me` (`get_signed_in_handler`) returns the handler
      for a valid token.
- [x] `backend/auth.py` checks the token with PyJWT's `PyJWKClient` on
      `{VITE_SUPABASE_URL}/auth/v1/.well-known/jwks.json`, audience `authenticated`, and finds the handler by
      the token's email. Tests sign tokens with a test key, served by a stub JWKS.
- [x] `start_dispatch`, `start_evaluation` and `sign_off` answer 401 with no token, an expired token or an
      unknown email. A test covers each.
- [x] `sign_off` takes no `handler_id`, records the signed-in handler, and answers 403 for Marcus Oyelaran
      with a message that says why.
- [x] The dashboard signs in with `@supabase/supabase-js` `signInWithPassword` from `VITE_SUPABASE_URL` and
      `VITE_SUPABASE_PUBLISHABLE_KEY`, sends the access token on every action, shows who is signed in in the
      header, and signs out. The sign-in page has no sign-up.
- [x] A claim waiting for sign-off shows Marcus a note saying he can't sign off over the limit, in place of
      the button.
- [x] `e2e/auth-stand-in.mjs` signs tokens with its own key, serves its JWKS and answers CORS preflights.
      Every spec signs in first. `npm run check` is green with `.env` moved away.
- [x] In the browser (on `npm run dev` with local Supabase), Dana signs in and sees her name in the header;
      a wrong password shows an error that says what to do.

## Phase 2: Settings and the Settings page

Settings live in the database, and every part of the product reads them from there.

- [x] `settings` seeded from `data/company.json` (approval limit) and `data/improvement.json`;
      `data/models.json` holds the models allowed. `GET /api/settings` (`get_settings`) returns them with
      the models allowed and the latest change to each.
- [x] `PUT /api/settings` (`change_settings`) needs a handler who can sign off over the limit, validates
      each value, and records who changed what and when. 401 and 403 tests, including Marcus.
- [x] A test: a new approval limit applies to the next decision, and a claim already waiting for sign-off
      stays waiting (`needs_sign_off` on the decision).
- [x] With `MODEL_NAME` set, the models allowed are that model alone, at effort `default` and price 0. A test.
- [x] The Settings page (`#/settings`) shows every setting, the models allowed with their efforts and prices,
      and who last changed each; Marcus sees it read only, with a note saying why.
- [x] A change asks for confirmation in the dashboard's own dialog, then saves and shows the new value.
- [x] The appearance (light, dark, the computer's) switches the colors at once and survives a reload. Vitest
      covers the switch.
- [x] `npm run check` is green; an end-to-end test changes a setting and switches the theme.

## Phase 3: The configuration in versions

The claim agent runs on a version of its configuration, and the Configuration page replaces Instructions.

- [x] Seeding makes version 1 from `backend/instructions/claim.md` with `openai/gpt-6-luna` at `low`, and
      recipe version 1 from `backend/instructions/recipe.md`. `npm run seed` takes both back to version 1
      and empties the rounds.
- [x] `GET /api/configuration/current` (`get_current_configuration`) gives the claim agent its version's
      instructions, model and effort, with no scores. The service builds each run's claim agents from the
      run's version and records it on the run.
- [x] A dispatch and an evaluation each record their version. A test for each.
- [x] `GET /api/configuration/versions` (`list_versions`) lists every version with what changed, the
      reason, who proposed and approved it and when, its held-out scores, and the diff from the version
      before, worked out with `difflib`.
- [x] Without `MODEL_NAME`, the effort is recorded as the version's; with it, as `default`.
- [x] A test: nothing the claim agent's client calls reaches `answer_key`, the scores, the rounds or the
      settings.
- [x] The Configuration page (`#/configuration`) shows the current version and every version with its
      held-out scores, and opens a diff of any version against the one before.
- [x] `npm run check` is green.

## Phase 4: The judge

Each claim in an evaluation is graded against the handler's reason, in the backend.

- [x] Seeding stores `reason` and `reference` in `answer_key` from `data/evals/`.
- [x] `backend/judge.py` calls `typesafe/jev-1.13` once through the `openrouter` package's
      `alpha.decisions.create`, with the lab's three questions, `server_url` read once from the environment,
      a 10-second timeout and retries off. The letter is the most likely level, never the score rounded.
- [x] `finish_run` grades an evaluation's claims in parallel and stays under 20 seconds with a stub judge
      that sleeps. A claim the agent didn't decide gets an F without a call; a claim the judge can't grade is
      not graded and left out of the average. Tests for each.
- [x] The judge's cost is recorded with the evaluation, apart from the claim agent's cost.
- [x] With `MODEL_NAME` set, the judge isn't called and every claim shows as not graded.
- [x] The Scoreboard adds the average grade; an evaluation's page shows each claim's grade, its letter and
      the two checks as yes or no.
- [x] Tests never call the judge: unit tests stub `judge.grade`, and the end-to-end tests point the address
      at `e2e/judge-stand-in.mjs`.
- [x] `npm run check` is green.

## Phase 5: Rounds in the backend: the rule, the scores and the spending

The backend owns the rounds, the rule that keeps or rolls back a version, and the round's spending.

- [x] `start_round`, `stop_round`, `get_round`, `list_rounds`, `approve_proposal` and `reject_proposal` need
      a handler who can sign off over the limit; Marcus gets 403 on each. Only one round is open at a time.
      (Built: the four actions need one; `get_round` and `list_rounds` are reads open to every page, as the
      brief has Marcus see a round and why he can't act on it.)
- [x] A round copies the settings at its start; a test changes a setting mid-round and the round keeps its
      own.
- [x] Rule tests, with measures worked by hand: a version kept, one rolled back for a measure worse than its
      tolerance, one rolled back for no gain above the noise, and one with no grade rolled back. Cost and time
      compare as fractions of the old value; accuracy and the grade as differences.
- [x] A version's scores average only the whole-set evaluations that rounds ran on it. A test.
- [x] The improver's view (`GET /api/rounds/{round_id}/results`) has the evaluation set's wrong claims and
      the five weakest right ones with the handlers' reasons, and nothing from the held-out set but its
      scores. A test checks for every held-out claim id and reason.
- [x] An evaluation that would go over the round's limit is refused; one cut short by the limit shows as
      stopped, and its version is rolled back. Tests for each.
- [x] A stopped round refuses anything it records afterwards, and leaves the current version as it was. A
      test.
- [x] `npm run check` is green.

## Phase 6: The improver

`agent/improver.py`, the sixth part of the agent, follows its recipe and pauses for a handler.

- [x] `create_agent` on GPT-6 Luna at `medium` (or `MODEL_NAME`), with `parallel_tool_calls=False`,
      `ModelCallLimitMiddleware` at 30, and `HumanInTheLoopMiddleware` on `propose_change` and
      `propose_recipe`, saving to its own `PostgresSaver` with `durability="sync"` and a new thread per round.
- [x] Its instructions are goals that wrap the recipe, say what the claim agent works from, and say that a
      change never names a claim. Its four tools are the lab's, through `agent/improver_api.py`.
- [x] A test on a stub model plays a whole round: the first evaluations run, a proposal waits and changes
      nothing until it's approved, the evaluations run once and twice, the verdict is recorded, and the
      recipe proposal waits.
- [x] Tests: a rejection's reason reaches the improver, which proposes again; a proposal outside the models
      allowed goes back to it without reaching a handler; a round with no proposal finishes with no version
      tried.
- [x] A test: an approval while a dispatch runs is refused, naming what is running, with nothing recorded.
- [x] `run_evaluation` works through the dispatcher in a fresh `contextvars.Context`, with the version under
      test, and the claims are tagged with the round as well as the run in Langfuse. The improver's work goes
      in one Langfuse session named after the round.
- [x] The improver's model calls are recorded with its round, with OpenRouter's `usage.cost`.
- [x] `npm run check` is green.

## Phase 7: The Improvement page, and the look of the whole dashboard

A handler runs rounds from the dashboard, and every page meets point 8 of the brief.

- [x] The Improvement page (`#/improvement`) starts and stops a round, with confirmation, and shows its
      stage as it happens: the first evaluations (1, 2 or 3 of 3), the improver working, a change waiting,
      the evaluations, the verdict, a recipe waiting, finished or stopped.
- [x] A proposal shows the diff, the model and effort before and after, and the reason, with Approve and
      Reject (the reason required). Marcus sees why he can't act.
- [x] The rounds so far, newest first, with the scores before and after, the verdict and why, and the cost;
      an SVG chart of each version's held-out accuracy and grade; the recipe's versions. One diff component
      serves versions and recipes.
- [x] Every page has the same header (title and what the page is for), spacing, navigation that shows where
      you are, right-aligned numbers with units, statuses as a color and a word, and loading, empty and
      error states that say what to do next. Nothing jumps as a page refreshes.
- [x] Nothing on the existing pages changes how it works: their tests pass unchanged except for the sign-in.
- [x] The end-to-end tests sign in, approve a proposal played out through the API, change a setting and
      switch the theme.
- [x] `npm run check` is green.

## Phase 8: The live runs and the documents

With the real model, after one `npm run seed`, with `npm run dev` and local Supabase running.

- [x] Signed in as Dana, two rounds from the Improvement page: the first proposal rejected with a reason,
      then the rest approved. Each round shows as it happens, then its scores before and after, verdict and
      cost.
- [x] The Configuration page shows the versions and their diffs, and through Langfuse's API each round's
      improver traces are found in one session.
- [x] Report how far version 1's two held-out runs differed on each measure, beside the noise and tolerance.
- [x] A dispatch at N=25 on the current version. As Marcus, a payout waiting for sign-off can't be signed off
      and the settings can't be changed; as Dana, the same payout is signed off and paid.
- [ ] Screenshots of every page in light and dark mode; a fresh subagent reviews them once as a senior
      product designer against `docs/BRAND.md` and point 8, and what it finds is fixed.
- [x] What the live checks spent, under $1 on GPT-6 Luna.
- [x] `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md` (rules 7 and 13 for the
      improver, its client and the dashboard's Supabase client), `docs/STATUS.md` and `data/README.md`
      describe the product as it now is.
- [x] `npm run check` is green with `.env` moved away.
