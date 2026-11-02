# Plan: security changes before UnderCover goes on AWS

The brief is `docs/SECURE.md`. The agent's side of the platform gets an API key that the backend checks,
a second model reads each claim for hidden instructions before the claim agent sees it, the backend
serves an API document with only the claim agent's operations and pays a claim at the handler's
sign-off, and the backend and the dashboard become one package ready for AWS Lambda. Nothing in this
build touches AWS.

SIDENOTE: For consistency with the course, please read `labs/week3/week3day2_lab1.ipynb` and build the
check in step 2 in the same style as the lab, and read `labs/week3/week3day2_lab2.ipynb`, which deploys
the app, without running it. Where the lab and this document differ, follow this document. Include this
sidenote in your plan too.

DATABASE NOTE: As in `docs/DURABLE_LOCAL.md` and `docs/PLAN_IMPROVE.md`, we use Postgres running locally
in Docker, not Supabase. The database is the `undercover-postgres` container (Postgres 17) on
`127.0.0.1:5432`. Sign-in uses Supabase Auth from Supabase's own stack on this machine, on
`127.0.0.1:54321`, with no Supabase account (`docs/LOCAL_LABS.md` section 4). Wherever the brief says
Supabase, read local Postgres for the data and the local stack for sign-in. Every phase below builds and
checks on this machine alone.

## Decisions for you before the build

These two are open. The brief assumes a hosted Supabase database, and this machine doesn't have one.

### A. Which database the deployed app will use

The brief puts the app on Lambda "with the database still on Supabase". Code on Lambda can't reach
`127.0.0.1` on this laptop. That covers two things, not one:

- **The data.** Lambda needs a Postgres it can reach, through a pooler on port 6543.
- **The sign-in.** The backend checks each handler's token against
  `{VITE_SUPABASE_URL}/auth/v1/.well-known/jwks.json`. On Lambda, the local stack's address is
  unreachable, so no handler could sign in to the deployed app.

The options:

1. **A free hosted Supabase project, for the deployed app only.** It gives both a database with the
   transaction pooler on 6543 and Supabase Auth with its public keys. The three handlers would be added
   to its Auth, and `npm run seed` run once against it. This is what the course's labs and briefs
   expect from here on.
2. **The course's free route on Render** in place of AWS. It still needs a database and an Auth the
   cloud can reach, so it doesn't remove the decision. It only changes where the app runs.
3. **Build and test everything locally now, and leave the deploy until a database is chosen.** This
   build never deploys anything, so it doesn't need the answer.

**My recommendation: 3 now, then 1 before Lab 2.** Nothing in this plan depends on the choice, so the
build can start today. When you run Lab 2, a free Supabase project is the one option that gives Lambda
both a database and a sign-in it can reach. It also matches every later brief and lab. You would then
also choose whether the laptop's `npm run dev` moves to that project too, so the agent on the laptop
and the deployed dashboard see the same claims, as Lab 2 assumes.

### B. What answers on port 6543 for the local check

Step 4 asks for "the backend started on the laptop with `AWS_LAMBDA_FUNCTION_NAME` set and
`DATABASE_URL` on port 6543". Nothing listens on 6543 here. The options:

1. **A PgBouncer container in transaction mode on `127.0.0.1:6543`**, in front of
   `undercover-postgres`. Like Supabase's transaction pooler, it doesn't support prepared statements,
   so it really tests the change. It is a local Docker container, not an AWS resource.
2. **A plain port forward from 6543 to 5432.** It only shows the backend accepts the new address. It
   wouldn't catch prepared statements left on.
3. **Skip the check** until a hosted database exists.

**My recommendation: 1.** It is the only local option that proves what the check is for. I'll make the
container only once you say yes, and the plan says how to remove it.

Watch out: our store picks the `undercover` schema with a `search_path` option on each connection. I
haven't confirmed that Supabase's transaction pooler (or PgBouncer) passes that option through. If the
6543 check shows the tables aren't found, the fix is SQLAlchemy's `schema_translate_map` on Postgres,
which names the schema in each query and works through any pooler. I'll raise it with you before making
that change.

## Choices for you

Review these too. The documents in `docs/` don't make these decisions.

1. **The eight operations' names.** The brief lists `search_documents` and `calculate_payout`. Our claim
   agent has no search: it uses `list_sections` and `read_section`. Its payout tool is
   `calculate_payout`, but the backend's operation is `work_out_payout`. Our agent's own eight are
   `get_claim`, `get_policy`, `get_claim_history`, `list_sections`, `read_section`, `work_out_payout`,
   `record_decision` and `pay_claim`. I recommend:
   - **Rename `work_out_payout` to `calculate_payout`**, so the Gateway's tool has the name the claim
     agent's instructions already use.
   - **Keep `list_sections` in place of `search_documents`.** Adding a search would change the claim
     agent's tools, which the brief says stay the same.

   Watch out: Lab 2 calls `search_documents` through the Gateway. That cell would fail on our build;
   `list_sections` is the call to use there.
2. **The brief's notes follow Ed's build, not ours.** I follow their intent:
   - The agent's one client is `backend()` in `agent/main.py`, not `agent/connection.py`. It is called
     for every request, so it reads `UNDERCOVER_API_KEY` as each request is sent, and the key reaches
     the command line after it loads `.env`.
   - All routes are in `backend/main.py`. The handler's sign-in is `signed_in_handler` (`Handler`) there.
     Seeding and the pool are in `backend/store.py` (`seed`, `postgres_engine`, `POOL_SIZE = 4`).
   - The claim graph is `claim_graph` in `agent/main.py`. The sign-off is `sign_off` (not
     `sign_off_claim`), and the service's resume is `carry_on_claim` in `agent/service.py`.
   - There is no startup hook. The store creates its tables and seeds on its first connection, in
     `store.engine()`. On Lambda that must not happen (choice 9).
   - **The dashboard does not send the handler's token on its reads** today, only on its actions.
     `getJson` in `dashboard/src/api.ts` gets the token too, or every page would get a 401.
   - There is no `e2e/api.ts`, `agent.spec.ts` or `finishOpenRound`. Our specs call the agent's routes
     directly with Playwright's `request`. A small helper gives those calls the test backend's fixed key.
3. **Who may call what.** Each route belongs to exactly one group, by who calls it today. A test lists
   every route in the app and fails if one is in no group or in two.

   | Group | Routes |
   |---|---|
   | Open | `health` |
   | The key or a handler (reads both use) | `get_claim`, `get_policy`, `get_claim_history`, `list_recipes`, and the new `get_agent_openapi` |
   | A handler (the dashboard) | `list_claims`, `list_steps`, `get_settings`, `get_current_run`, `list_versions`, `list_evaluations`, `get_evaluation`, `list_handlers`, `get_signed_in_handler`, `list_rounds`, `change_settings`, `start_dispatch`, `start_evaluation`, `sign_off`, `start_round`, `stop_round`, `approve_proposal`, `reject_proposal` |
   | The key (the agent's side) | `list_sections`, `read_section`, `calculate_payout`, `record_decision`, `pay_claim`, `start_claim`, `release_claim`, `add_step`, `record_model_call`, `record_work_time`, `get_current_configuration`, `get_run_configuration`, `create_run`, `list_run_claims`, `finish_run`, `stop_running_runs`, `get_round`, `read_round_results`, `record_round_stage`, `record_proposed_change`, `record_proposed_recipe`, `record_round_decision`, `record_improver_call`, `create_round_evaluation`, `apply_verdict`, `stop_working_rounds`, `list_waiting_claims`, `list_unfinished_claims`, `get_policy_wording` |

   The last three have no caller today. They go with the key, the stricter group.
4. **The key check reads the request's headers itself**, through a small dependency that takes the
   `Request`. FastAPI then adds no header parameter or security scheme to the operations, so the agent's
   document has no key input for the Gateway to turn into a tool input. The shared reads use the same
   kind of dependency for the key or the token. When `UNDERCOVER_API_KEY` is unset or empty, the key
   check refuses everything, so an empty header can never match an empty key.
5. **The check is the claim graph's first node, `check`**, in `agent/main.py` beside the graph. It
   fetches the claim itself with `get_claim` and gives the check model only the fields the customer
   wrote (the claim as filed, without our own columns such as status and decision). It uses the lab's
   `SCREEN_INSTRUCTIONS` and `Screening` model (`reason`, then `instructions_found`), through
   `with_structured_output(Screening, method="json_schema", include_raw=True)` so each call's cost is
   recorded. It is a constant in the code, not in the versioned configuration, so the improver can't
   reach it.
6. **`MODEL_NAME` is read in one place in the agent.** Today `agent/main.py` and `agent/improver.py`
   each read it. One small function in `agent/main.py` reads it, and the claim agent, the improver and
   the check all use it. The backend is a separate package and keeps its own reads.
7. **What the check records.** Its result is the claim's first step, as a `tool_result` step named
   `security_check`, so the claim page shows it with no change to the dashboard. A flagged claim is
   recorded as a referral with payout 0, the check's reason, and the section "Security check". The
   graph then ends without calling the claim agent's model.
8. **The sign-off pays, and the agent still finishes its thread.** `sign_off` records the handler, the
   time and the payment in one transaction, and the claim is decided. The backend still asks the
   agent's service to resume the claim, as a best-effort call that a missing service never fails. The
   graph's `sign_off` node now goes straight to the end, so the resume only records "Signed off by" and
   finishes. The service's resume accepts a claim that is signed off and paused at the sign-off. This
   keeps the paused threads from being left half-finished in the saver.
9. **Lambda mode.** When `AWS_LAMBDA_FUNCTION_NAME` is set, the store:
   - uses SQLAlchemy's `NullPool`, so each request opens its own connection and keeps none;
   - passes `prepare_threshold=None` to psycopg, so no statement is prepared;
   - doesn't create the schema or the tables and doesn't seed, so nothing writes on its first request.

   Without that variable, the pool of 4 and the seeding stay as they are.
10. **The dashboard's build is served by a small `StaticFiles` subclass** mounted at `/` after every
    route, only when `dashboard/dist` exists. A file that exists is served. Any other path outside
    `/api/` gets `index.html`, and an unknown `/api/` path gets FastAPI's JSON 404. A mount isn't an
    operation, so the API rules don't apply to it and it isn't in any OpenAPI document.
11. **The payment's limit reads today's setting**, as the brief says. Watch out: `docs/SPEC.md` says a
    new limit applies only to decisions recorded after it. If the limit is lowered between an approval
    under the old limit and its payment, the payment is now refused and the claim stays unpaid. That
    window is a few seconds inside one claim's work, so I'll note it in SPEC.md rather than build around
    it.
12. **FastAPI's own `/docs` and `/openapi.json` stay as they are.** On a public address they describe
    every route, but they hold no data and the brief doesn't mention them. Say if you'd like them off on
    Lambda.
13. **Browser checks use the Chrome DevTools MCP server**, as in the last two builds, because the
    Playwright MCP server isn't connected here. In development the dashboard is opened at
    `http://[::1]:5173`, because another app holds `127.0.0.1:5173`.
14. **The package size check is a one-off**, run with `uv run python` from a scratch folder, as Lab 2
    builds it: `uv export --only-group server`, `uv pip install --target` with Linux wheels for Python
    3.12, then the backend, `data/` and `dashboard/dist`, zipped. Nothing is committed for it, since Lab
    2 builds the real package.

## Phase 1: An API key for the agent

The agent's side sends the key on every request, and the backend checks it, or a handler, on every
route but `health`.

- [x] `.env` has `UNDERCOVER_API_KEY`, made with `secrets.token_urlsafe(32)` if it was missing, and never
      printed. A script prints only that it is there and its length, at least 32.
- [x] `backend()` in `agent/main.py` sends `X-API-Key` with the key read as each request is sent. A test
      sets the key after the agent is imported and finds it on the request. The claim agent, the
      dispatcher, the improver's client and the service all use `backend()` (test reads their source).
- [x] An agent route (`record_decision`) answers 401 with no key and with a wrong key, and 200 with the
      right key. The check uses `secrets.compare_digest` (tests).
- [x] A shared read (`get_claim`) answers 200 with the key and with a handler's sign-in, and 401 with
      neither. A dashboard read (`list_claims`) answers 401 with only the key (tests).
- [x] Every handler action (sign-off, starting a dispatch, an evaluation or a round, approving or
      rejecting a proposal, changing a setting) answers 401 with only the key (one parametrized test).
- [x] Every route is in exactly one group of choice 3, and `health` answers without either (test).
- [x] With `UNDERCOVER_API_KEY` unset, the backend starts and a handler can read and act, every key
      route answers 401 even with an empty `X-API-Key`, and the agent's service won't start and says the
      key is missing (tests).
- [x] The operations' OpenAPI entries have no key or token parameter (test). The dashboard's reads send
      the handler's token (vitest). The e2e backend has a fixed key that the specs' agent calls send.
      `npm run check` is green with `.env` moved away.

## Phase 2: A check for instructions hidden in a claim

The claim graph's first node reads the claim on a second model and refers a claim that tries to instruct
whoever handles it.

- [x] The `check` node runs on `openai/gpt-6-luna` at reasoning effort `none`, or on the model in
      `MODEL_NAME` with no effort set, read through the agent's one reader of `MODEL_NAME` (test on the
      model's settings).
- [x] A claim the stub check flags is referred with payout 0, the check's reason and the section
      "Security check", and the claim agent's stub model is never called (test).
- [x] A claim the stub check passes is worked as before. The check's result is the claim's first step
      (test).
- [x] An answer that can't be read is tried again, up to three attempts. Each attempt is recorded as one
      of the claim's model calls with its cost, so it counts in the claim's cost on the scoreboard
      (test).
- [x] A check that fails three times, or raises an error, never reaches the claim agent's model, and the
      claim fails as any other error does: back in the queue, then left for a handler (tests).
- [x] A claim carried on from saved progress, or resumed after a sign-off, isn't checked again. A claim
      started again on a new thread is (tests count the check's calls).
- [x] `npm run seed` puts CLM-1901 at the end of the queue with its policy and customer from
      `data/security/`, without its handler's decision. `data/claims.json` is unchanged. CLM-1901 is in
      neither evaluation set, tiny or whole (tests).
- [x] The check is not in the configuration, and nothing the improver proposes can change it (test on
      the configuration's fields). `npm run check` is green.

## Phase 3: The agent's own API, and the sign-off that pays

The backend serves the claim agent's operations as their own document, the payment refuses any amount
over the limit, and a handler's sign-off pays the claim.

- [x] `GET /api/agent/openapi.json` (`get_agent_openapi`) answers with exactly `get_claim`, `get_policy`,
      `get_claim_history`, `list_sections`, `read_section`, `calculate_payout`, `record_decision` and
      `pay_claim` (or the names you choose in choice 1), and no `servers` entry (test).
- [x] In that document each operation keeps its `operation_id` and its one-line docstring, the payment's
      input is still `amount`, and no operation has a key or token parameter (test).
- [x] The document answers with the key or a handler's sign-in, and 401 with neither (test).
- [x] `pay_claim` refuses an amount over the approval limit with a 403 that names the limit, before it
      reads the claim: the same answer on a claim that was never approved and on one that doesn't exist
      (tests).
- [x] A sign-off pays the claim once and leaves it decided, with the handler, the time and the payment,
      with no agent's service running. A second sign-off is refused (tests).
- [x] A claim paused at the sign-off and resumed after it finishes with no call to `pay_claim`. A claim
      approved within the limit is still paid by the graph (tests that record the agent's requests).
- [x] The claim page shows a signed-off claim as paid and decided straight away (vitest). The e2e claim
      spec signs off and sees "Paid $1,250.00" with no payment from the test.
- [x] `npm run check` is green.

## Phase 4: Ready for Lambda

One command builds the dashboard, the backend serves it, a Lambda handler wraps the app, and the
database connections suit Lambda.

- [x] `npm run build` writes `dashboard/dist` with no warnings, with the Supabase address and key from
      `.env`. With `.env` moved away and the two variables set in the shell, it builds them in from the
      environment. Vitest and the e2e dashboard still read no `.env` (`envDir` stays off outside
      development and the build).
- [x] With a stand-in build in a temporary folder, `/` and a dashboard page such as `/claims` get
      `index.html`, a file gets itself, every `/api/` route works as before, and an unknown `/api/`
      path gets a JSON 404. Without the folder, nothing is mounted (tests).
- [x] `handler` at the end of `backend/main.py` is `Mangum(app, lifespan="off")`. A test calls it with a
      Function URL event for `/api/health`, and with the stand-in build for `/` and a dashboard page.
- [x] With `AWS_LAMBDA_FUNCTION_NAME` set, the store uses `NullPool` with `prepare_threshold=None`, opens
      a connection for each request (a test counts them over two requests), and doesn't create tables or
      seed. Without it, the pool of 4 is unchanged (tests).
- [x] The backend reads nothing from `agent/` and no file outside `backend/`, `data/` and
      `dashboard/dist`, and writes no file in Lambda mode (test reads its imports and file paths).
      Everything else it reads from the environment has a default, or is needed only by work that can't
      start on Lambda.
- [x] `npm run check` includes `check:server`:
      `uv run --isolated --only-group server python -c "import backend.main"`, which leaves `.venv` as it
      is. The `server` group lists what the backend imports. It passes, and `npm run check` is green.
- [x] `docs/TECH_STACK.md` has a Deployment section: `npm run build` and `dashboard/dist`, what goes in
      the package, `backend.main.handler`, `backend.main:app` for a web server, and the three
      environment variables.

## Phase 5: The live checks and the documents

With GPT-6 Luna, after one `npm run seed`, with `npm run dev` and local Supabase running.

- [x] Step 1: signed in as Dana, the dashboard works as before. `uv run --env-file .env -m agent
      CLM-1001` decides CLM-1001. `curl.exe` to `record_decision` with no key gets a 401.
- [x] Step 2: a dispatch from the queue page works the rest of the queue as before and refers CLM-1901
      with the check's reason, which its claim page shows as its first step. Report each claim's decision
      against the swarm build's dispatch.
- [x] Step 2: an evaluation of the evaluation set's tiny five flags none of them.
- [x] Step 3: Dana signs off a held payout from that dispatch on the dashboard, and it is paid once
      (checked in `undercover.claims` with `docker exec undercover-postgres psql`).
- [x] Step 4, after `npm run build` and with `npm run dev` stopped: `uv run -m uvicorn backend.main:app`
      serves the dashboard at `http://127.0.0.1:8000`, where Dana signs in and sees the claims, through
      the Chrome DevTools MCP server.
- [x] Step 4, if decision B allows: the backend with `AWS_LAMBDA_FUNCTION_NAME` set, `DATABASE_URL` on
      port 6543 and no other UnderCover variable but the Supabase address and the key, serves the claims
      to Dana's token. Report the connections in `pg_stat_activity` before and after.
- [x] Step 4: the package, built in a scratch folder with Linux wheels for Python 3.12, is under 50 MB
      zipped. Report its size. What the live checks spent, from the costs in the database.
- [x] `docs/SPEC.md` (the key, the check, the sign-off that pays, CLM-1901 in the queue),
      `docs/TECH_STACK.md`, `docs/PROCESS.md` (the key, `npm run build`, `check:server`),
      `docs/CODE_STANDARDS.md` (the check in the claim graph), `docs/STATUS.md` and `data/README.md`
      describe the product as it now is. `npm run check` is green with `.env` moved away.

If decision B is option 1, the PgBouncer container is removed after the check with `docker rm -f`, unless
you'd like to keep it.

## Notes from the build

Phase 1:

- Until Phase 3 renames it, the payout operation in the key group is still `work_out_payout`; the
  access test lists it under that name.
- FastAPI 0.141's `HTTPBearer` already answers 401 for a missing token, so the handler routes keep it.
  It puts a bearer security scheme (not a parameter) on the handler operations in `/openapi.json`.
  The key check and the shared reads' check read the headers themselves, so the key routes and the
  shared reads have neither a parameter nor a security entry, which keeps Phase 3's document clean.
- The tests' clients send the agent's key by default (`AGENT` in `backend/tests/signing.py`, set as
  `UNDERCOVER_API_KEY` for every test in `conftest.py`), and their dashboard reads send Dana's
  sign-in. `backend/tests/test_access.py` uses a client with neither, and also checks that every
  route but `health` answers 401 to a request with neither.
- The agent's tests swap `backend()` for an in-process client built with `main.key_header()`, so the
  real header code still runs; one test keeps the real `backend()` and finds a key set after import
  on its request.
- The e2e specs read `/api/rounds` and `/api/settings` outside the page, which now need a handler.
  `asHandler` in `e2e/agent.ts` signs in through the Supabase Auth stand-in for those.

Phase 2:

- The check is the graph's `check` node, a module function in `agent/main.py` beside `screen()`, which
  fetches the claim, drops the backend's own fields (`BACKEND_FIELDS`) and calls the model. Its result
  is recorded with `record_step` rather than as a message, so nothing is added to the claim agent's
  conversation: a tool result with no tool call before it would be refused by the model's API.
- LangChain's base `with_structured_output` works on the tests' stub model by binding the `Screening`
  schema as a tool, so the stub answers with a `Screening` tool call; an answer with no tool call
  comes back with `parsed` empty, which the check treats as unreadable, as it does a parsing error
  from the real model's JSON schema.
- A check that raises a rate limit or timeout from the model library (`ModelRateLimitError`,
  `ModelTimeoutError`) is let through to the dispatcher as the claim agent's own are, so the claim
  carries on and the check runs again; any other error fails the claim.
- With CLM-1901 seeded, the queue has 26 claims: the tests that counted 25 in the queue, the
  dispatcher's runs, a dispatch's listed claims and the e2e queue page now count 26. The evaluation
  sets stay at 25 and 5.
- `npm run seed` wasn't run against the real database in this phase; the seed criteria are checked
  by tests on SQLite. The live dispatch is Phase 5.

Phase 3:

- The payout operation is now `calculate_payout` in the backend too (the route function and the
  operation), so the access test, the payout test and the agent's test of the operations it reaches
  use that name. `backend/payout.py` keeps its own `work_out_payout`, which isn't an operation.
- The limit check put first in `pay_claim` changed an older test: a claim approved for $420 under
  the $1,000 limit, then the limit lowered to $100, was paid before and is now refused (choice 11).
  The test now expects that, and `docs/SPEC.md` says so.
- The sign-off sets `paid_at` to the same moment as `signed_off_at`. The service's resume now takes a
  claim that is signed off (decided) as well as one in progress; the graph's `sign_off` node goes to
  the end, so the resume records "Signed off by" and makes no request to the payment.
- A dispatch lists only claims in progress to carry on, and a signed-off claim is now decided, so a
  paused thread is finished only by the service's resume after the sign-off. If the service is down
  at that moment, the thread stays paused in the saver; the claim itself is paid and decided.

Phase 4:

- `vite build` runs in the `production` mode, so `envDir` is the repo root in `development` and
  `production` only. Vite always adds `VITE_` variables from the shell, over `.env`, so a build with no
  `.env` takes them from the environment; Vitest (`test`) and the e2e dashboard (`e2e`) still read no
  `.env`. The build is 485 kB of JavaScript, just under Vite's 500 kB warning: a few more dependencies
  would bring the chunk-size warning back.
- `dist/` was already in `.gitignore`, so `dashboard/dist` is ignored, and ESLint already ignores
  `**/dist`; Prettier skips it through `.gitignore`.
- Starlette's `StaticFiles` hands `get_response` a path with the system's separators (backslashes on
  Windows), so the `/api/` test reads the request's own path, not that argument.
- With the mount at `/`, a GET to an `/api/` path that only has a POST route now gets a JSON 404
  instead of a 405, because the mount matches it before the router's method check. Every route and
  method that exists answers as before: the whole Python suite passes with `dashboard/dist` built.
- In Lambda mode SQLite also uses `NullPool`, so the tests count one connection a request on SQLite;
  the Postgres settings (`NullPool`, `prepare_threshold=None`, the `search_path` option) are checked
  on the arguments given to `create_engine`, with no database. Off Lambda the two requests open no
  new connection: they reuse the one the seeding opened.
- Mangum 0.22 calls `asyncio.get_event_loop()`, which warns on Python 3.12 outside a running loop. It
  is only a warning, in the tests.
- The `server` group already listed what the backend imports (`pydantic` and `starlette` come with
  `fastapi`); `check:server` installs 27 packages in a throwaway environment and takes about 30 seconds.
- Not a Phase 4 box: the package built as Lab 2 builds it, in a scratch folder with Linux wheels for
  Python 3.12, is 20.3 MB zipped. That build skipped `*.db`; Lab 2's `copytree` doesn't, so a local
  `backend/undercover.db` would go into its package unless it is deleted first. `data/evals/` goes in
  too, as Lab 2 copies all of `data/`; the backend reads it only when seeding, which never happens on
  Lambda.

Phase 5:

- The swarm build's dispatch, for the comparison, is the one that was in Postgres before the reseed
  (run 1 stopped at N=3, run 2 at N=25), saved before `npm run seed` emptied it. 21 of the 25 queue
  claims got the same decision and payout; CLM-1012, CLM-1018, CLM-1021 and CLM-1025 differed, each
  passed by the check (its step says "No instructions found"), so the claim agent itself decided them
  differently. `docs/STATUS.md` has each.
- The dispatch ran at N=25, to match the swarm build's, and the evaluation of the tiny five at N=5.
- The sign-in on the dashboard was filled by a script in the page that fetched `HANDLER_PASSWORD` from
  a one-off local helper, so the password never appeared in a command or a tool call. The 6543 check
  signed in to Supabase Auth from a script the same way.
- Decision B, option 1: `edoburu/pgbouncer` (PgBouncer 1.26.1) as `undercover-pgbouncer`, transaction
  mode, `max_prepared_statements = 0`, on `127.0.0.1:6543`, reaching `undercover-postgres` on Docker's
  bridge. The `search_path` option on each connection came through: the tables were found though the
  server's own `search_path` is `"$user", public`. So no `schema_translate_map` change was needed. This
  was PgBouncer, not Supabase's own pooler, which still has to be tried at deploy time. The container
  was removed with `docker rm -f` after the check.
- The tiny evaluation has no grades: the judge's calls returned nothing (`judge.grade` swallows
  OpenRouter's errors and answers it can't read), so its grade shows as not graded. No judge cost was
  recorded. It isn't a box of this build and wasn't looked into.
- The package size is the Phase 4 build's, 20.3 MB zipped, not rebuilt; nothing it holds changed since.
