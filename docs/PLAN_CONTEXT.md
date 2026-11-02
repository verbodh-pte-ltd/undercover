# Plan: the context layer, memory and the Gateway for the claim agent

The brief is `docs/CONTEXT.md`. The claim agent's tools and the graph's payment can go through the
AgentCore Gateway, or straight to the app and a local context layer as today. Data governance's seven
files in `context/` become an MCP server of their own. Every decision record meets claims manual
section 14. The agent saves each decided claim to AgentCore Memory and can search what it remembers
about the customer. The claim page shows every step, with where each lookup came from. Nothing in this
build touches AWS: the Gateway, the memory and AWS's signing are all checked against stand-ins.

SIDENOTE: For consistency with the course, please read `labs/week3/week3day3.ipynb`, which deploys the
app and the context layer, creates the Gateway and the memory, and runs the agent against them, without
running it. Where the notebook and this document differ, follow this document. Include this sidenote in
your plan too.

DATABASE NOTE: As in `docs/DURABLE_LOCAL.md` and `docs/PLAN_SECURE.md`, we use Postgres running locally
in Docker, not Supabase. The database is the `undercover-postgres` container (Postgres 17) on
`127.0.0.1:5432`. Sign-in uses Supabase Auth from Supabase's own stack on this machine, on
`127.0.0.1:54321` (`docs/LOCAL_LABS.md` section 4). Wherever the brief says Supabase, read local Postgres
for the data and the local stack for sign-in. Every phase below builds and checks on this machine alone.

This plan builds on the product as `docs/PLAN_SECURE.md` leaves it at its end: the agent's API key, the
security check, the agent's own API document of eight operations, the sign-off that pays, and the app
ready for Lambda (`npm run build`, `backend.main.handler`, `check:server`).

## How this build runs

Written after the Secure build, where each phase took 30 to 40 minutes, mostly in repeated checks.

- **One builder for the whole build.** One agent builds every phase in order, so the documents are read
  once, not once per phase.
- **While building a phase, run only the tests for what changed**, for example
  `uv run pytest agent/tests/test_agent.py -q --no-cov`, `npx vitest run --root dashboard <file>` or
  `npx playwright test e2e/<spec>.ts`.
- **Each phase ends with its commit.** The pre-commit hook runs the format, lint, type and unit checks
  for the part that changed, so a phase's last box is that the hook passes. No phase runs the whole
  `npm run check`.
- **The whole `npm run check` runs once, in Phase 6**, with `.env` moved away, as `docs/PROCESS.md` asks
  when the last phase is done. If it finds a break from an earlier phase, fix it there and say which
  phase it came from in the notes.

## Decisions for you before the build

### A. How the claim agent finds the sections it needs

The brief says "the claim agent's instructions still end with the contents of both documents". Ours
never did. Our agent gets no document whole: its instructions tell it to list a document's sections with
`list_sections`, then read the ones it needs with `read_section`. The brief moves the documents to the
context layer with `search_documents` and `read_section`, and has no `list_sections`. Without a list, our
agent would have to find sections by searching. The options:

1. **The context layer also serves `list_sections(document)`**, as the app does today. The agent keeps
   working the way it does now, and its instructions keep their "list, then read" order. It is one more
   read-only tool, beyond the seven the brief names.
2. **No list.** The agent finds sections with `search_documents` and reads them with `read_section`. Its
   instructions change to match. This is closest to the brief's tool list, but changes how every claim
   is worked, and could change the scores.
3. **Add both documents to the instructions**, as the brief describes. Every model call then carries
   about 300 lines of documents. That raises the cost of each claim several times over, and changes what
   the improver's versions mean.

**My recommendation: 1.** "Still" means the instructions stay as they are, and in our build that means
without the documents. Option 1 is the only one that leaves the agent's way of working unchanged while
the documents move. The app's agent document still drops to the brief's six operations, since
`list_sections` moves with the other two.

**Decided (2026-11-02): option 1.** The context layer also serves `list_sections`.

### B. What needs AWS, and the database the cloud can reach

No phase below needs AWS or a hosted database. Every criterion in the brief's "Done when" and "What done
looks like" runs on the direct route with memory off, and the tests use stand-ins.

These need AWS, and none of them is in this plan:

- **The Gateway route for real**: tools through `undercover-app___*` and `undercover-context___*`, and
  the Policy refusing a payment over $1,000. This needs the app on Lambda, the context layer on Lambda,
  the API key in AgentCore Identity, the Gateway with two targets, and a policy engine.
- **Memory for real**: events saved, memories extracted about 90 seconds later, and `search_memory`
  finding the first claim on the second. This needs one AgentCore Memory.

The Gateway route also needs decision A of `docs/PLAN_SECURE.md`, which is still open. The app on Lambda
must reach a database and a sign-in, and `127.0.0.1` on this laptop is out of reach from there.

The options:

1. **Build and check locally now, then run `labs/week3/week3day3.ipynb` against a free hosted Supabase
   project.** The lab creates every AWS resource above and its Part 6 tears down the AgentCore ones.
2. **Also check memory live during this build**, with one AgentCore Memory made for the purpose and
   deleted afterwards. It needs no Lambda and no hosted database, since memory is called from the laptop.
3. **Build and check locally only**, and leave AWS for later.

**My recommendation: 1.** It matches your standing decision: build and test locally now, and a free
hosted Supabase project before the AWS labs. Option 2 is cheap, but the lab does the same check a day
later with its own teardown. I won't create anything on AWS unless you ask.

**Decided (2026-11-02): the Gateway, the Policy and Memory for real, in this build**, in Phase 7
below, after the six local phases. **No Supabase project.** The deployed app's data goes in Amazon RDS for
PostgreSQL on the smallest instance, and its sign-in in an Amazon Cognito user pool. Everything Phase 7
makes on AWS is deleted when the lab is finished. AWS is reached through the `[default]` profile, the
`undercover-course` role from `labs/week3/week3day1.ipynb`, signed in through the `<AWS_ADMIN_PROFILE>` session.
That role has no RDS, Cognito or EC2 permissions today, so Phase 7 first adds them to the course policy
through the `<AWS_ADMIN_PROFILE>` administrator profile, limited to resources named `undercover...`.
Phases 1 to 6 don't depend on any of this. The laptop keeps local Postgres and local Supabase Auth.

Estimated cost of the lab, if you run it. These figures are from AWS's price list as I remember it, not
checked today, so check `uv run monitor.py` after the lab:

- Two Lambda functions, their logs and IAM roles: within the free tier, about $0.
- The API key in AgentCore Identity keeps a Secrets Manager secret: $0.40 a month, so cents for a day.
- Gateway tool calls: about $0.005 per 1,000. Two claims make about 40 calls.
- Memory: about $0.25 per 1,000 events saved, plus stored memories and searches at a similar scale. Two
  claims save 4 events.
- The policy engine: priced per authorization. I don't know its price; at a few dozen calls it should
  be cents.

Watch out: `labs/week3/week3day3.ipynb` still needs its `docs/LOCAL_LABS.md` pass (truststore, and the
database decision) before you run it. That is separate from this build.

## Choices for you

Review these too. The documents in `docs/` don't make these decisions.

1. **The brief's notes follow Ed's build, not ours.** I follow their intent:
   - There is no `agent/tools.py`, `agent/api.py`, `agent/harness.py` or `agent/connection.py`. The
     tools, the claim graph, the harness and `backend()` are all in `agent/main.py`. The step kinds are
     made in `steps()` there, checked by `Step` in `backend/main.py`, and typed in `dashboard/src/api.ts`.
   - There is no `backend/documents.py`, `backend/database.py`, `backend/models.py`, `backend/agent_api.py`
     or `backend/configuration.py`. The routes, `Decision`, `AGENT_OPERATIONS` and the section splitter
     are in `backend/main.py`. Seeding and the tables are in `backend/store.py`. The payout rules are in
     `backend/payout.py`, which reads `data/plans.json` when it is imported.
   - **There is no documents table.** The app reads `data/policy_wording.md` and `data/claims_manual.md`
     on each request. Phase 2 adds the table.
   - The claim page is `dashboard/src/ClaimView.tsx`, with its steps, sections and decision in that one
     file, and the sign-off in `SignOff.tsx`. There is no `ClaimPage.tsx`, `StepList.tsx`,
     `SectionsRead.tsx` or `DecisionPanel.tsx`.
   - The improver's sentence listing the claim agent's tools is in `INSTRUCTIONS` in
     `agent/improver.py`, not in `backend/instructions/improver.md`.
   - `conftest.py` doesn't yet note the asyncio trap. Phase 1 adds the note beside the test that guards it.
   - The claims in the context layer's data dictionary use some other names than our app: `copy_of`
     (ours `copied_from`), `evaluation_id` (ours `run_id`), `left_for_handler` (ours `needs_handler`),
     `decision.section` (ours `policy_section`), and a nested `decision` (ours is flat). The files are
     data governance's, so I don't change them, and renaming our fields would ripple through the
     dashboard, the evaluations and the judge. I keep our names, and the new fields take the
     dictionary's names (choice 8). Watch out: `describe_fields` will tell the model about names it
     won't find on `get_claim`. Worth raising with data governance.
2. **One module for each route**, as the brief asks `docs/CODE_STANDARDS.md` to say:
   - `agent/direct.py`: the app with the API key, as `call()` does today, and the context layer at
     `UNDERCOVER_CONTEXT` (or `http://127.0.0.1:8006/mcp`) over MCP with no signing.
   - `agent/gateway.py`: both targets through the Gateway at `UNDERCOVER_GATEWAY_URL`, signed with
     SigV4 for `bedrock-agentcore`, with credentials and region from `boto3.Session()`.
   - `agent/mcp_client.py`: what the two share. It opens an MCP session over streamable HTTP for each
     call, as the Gateway is stateless, and calls one tool.
   - Each route offers the same `use(tool, arguments)`, which returns the answer's text and whether it
     was refused. The claim graph picks a route when it is built. The bookkeeping stays on `backend()`
     and `call()` in `agent/main.py`.
3. **The MCP client is the `mcp` package's own `ClientSession`, not `MultiServerMCPClient`.** One tool
   call is one `call_tool`. Its `isError` flag is the app's or the context layer's refusal, and an
   `McpError` is the Gateway's refusal. That keeps the two kinds of refusal apart without the adapters'
   `handle_tool_errors` setting. The agent's own tools are thin `@tool` functions over `use()`, so they
   stay synchronous.
4. **The bridge from the synchronous agent to the async client** runs each call on a new event loop of
   its own (`asyncio.new_event_loop()`, `run_until_complete`, `close()`). It never sets or clears the
   thread's loop, so Mangum in the Lambda tests still finds one. A test runs a Gateway call, then the
   app's Lambda handler, in the same process.
5. **The stand-in Gateway in the tests** is a small ASGI app that answers MCP's JSON-RPC (`initialize`,
   `tools/list`, `tools/call`) with JSON. It runs on a free `127.0.0.1` port in a thread for the tests
   that need it. It:
   - names its tools `undercover-app___<operation>` and `undercover-context___<tool>`;
   - passes app tools to the app in process with the key, and context tools to the context layer's own
     functions;
   - answers an app refusal with `isError`, as the Gateway does;
   - refuses `pay_claim` over $1,000 with a JSON-RPC error starting "Tool Execution Denied", and can be
     told to refuse any named tool the same way;
   - keeps each request's headers, so a test can check the signature.

   The tests set fake AWS keys and a region in the environment, and point `AWS_CONFIG_FILE` and
   `AWS_SHARED_CREDENTIALS_FILE` at a missing file. This machine has an SSO profile in `~/.aws`, and no
   test may read it. Watch out: that an app refusal comes back from the real Gateway with `isError` is
   inferred from the brief's notes, not seen. The lab's cell that records a referral with a payout shows
   it.
6. **`calculate_payout` takes the claim, not the policy.** Its route becomes
   `POST /api/claims/{claim_id}/payout`, with `benefit`, `lines` and `paid_by_others`. The app takes the
   policy from the claim. The agent fills in the claim's id, so the model gives no id. Phase 3 keeps
   each calculation with the claim.
7. **The operation for a payment the Gateway refused** is `hold_for_sign_off`
   (`POST /api/claims/{claim_id}/hold`, no body, the key only, not in the agent's API document).
   - The brief says it moves "an approved, unpaid claim in progress". In our build an approval within
     the limit is already `decided`, unpaid, until the graph pays it. So it moves an approved, unpaid
     claim that needed no sign-off to `waiting_for_sign_off`, and anything else gets a 409.
   - It records why the claim waits, in a new column `held_by_gateway`. The sign-off panel then says
     the Gateway refused the payment.
   - Then the graph waits at its `sign_off` node, as for any payout over the limit. The sign-off pays,
     and the resume records "Signed off by" and makes no payment.
8. **The decision record stays flat on the claim**, as today. The new fields take the data
   dictionary's names: `calculation` (the lines and the payout, or none), `decided_by`, `decided_at` and
   `manual_version`. `policy_section` keeps its name. Watch out: the lab's cell that records a referral
   sends `section`, not `policy_section`. On our build it gets a 422 for the missing field, which still
   refuses, but for a different reason.
9. **The calculation for "this attempt"** goes in a column of its own, `latest_calculation`. It is
   written by every `calculate_payout` on the claim and cleared when an attempt starts on a new thread
   or the claim is released. An approval copies it into `calculation` on the record.
10. **Who decided.** The agent fills in `decided_by` as `UnderCover Agent, version <n>`, from the version
    the claim works to (its run's, or the current one). The check's referral sends `Security check`.
11. **The section 14 messages are our own.** `record_decision` checks the request itself before it
    reads the claim, so `detail` is one sentence such as "Claims manual section 14: a decision record
    needs a reason". FastAPI's own 422 for a missing field stays as it is, since the agent always sends
    every field.
12. **Steps carry what the page needs.** `Step` gets `detail`, a JSON object or `null`. Every step sends
    it, so the input is never optional. The new kinds are `lookup`, `memory_search`, `memory_save` and
    `refusal`.
    - The thin tools return their answer as content and their step as an artifact
      (`response_format="content_and_artifact"`). `steps()` reads the artifact.
    - A lookup's `detail` is what was looked up and each file it came from, with the file's system,
      owner and review date.
    - The claim view drops `sections_read`. The dashboard groups the claim's lookup steps by file for the
      Context used panel. The check's `BACKEND_FIELDS` loses `sections_read` and gains the new fields.
13. **The memory is its own module, `agent/memory.py`.** It has the store (`AgentCoreMemoryStore`, or a
    stand-in in tests), the save of the two events, and the search. The save is a graph node,
    `remember`, between the model's last reply and `settle`. It does nothing when memory is off, for an
    evaluation's copy, or when the claim has no decision. A claim the check referred never reaches it.
    "AgentCore can't be reached" means botocore's `BotoCoreError` or `ClientError`.
14. **`UNDERCOVER_API`.** The lab runs `uv run -m agent` with `UNDERCOVER_API` set to the app on Lambda,
    and our agent's address is fixed at `http://127.0.0.1:8000`. I'll have `backend()` and the direct
    route read `UNDERCOVER_API`, with that default, as each client is made. It isn't in the brief's
    steps, but without it Part 5 of the lab would record to the laptop's backend.
15. **Dependencies.**
    - `mcp` and `pyyaml` join the project's dependencies by name. Both are already in `uv.lock` (mcp
      1.30.0, pyyaml 6.0.3), through other packages.
    - `langgraph-checkpoint-aws` is new. If it needs other versions of our LangGraph packages, I'll stop
      and ask.
    - The `context` group is `mcp`, `pyyaml` and `mangum`.
    - The `server` group gains `pyyaml`, since seeding reads YAML and the store is imported by the app.
16. **`get_policy_wording`** has no caller. It now reads the wording from the documents table, so the
    app reads no document from `data/`.
17. **Browser checks use the Chrome DevTools MCP server**, as in the last builds, because the
    Playwright MCP server isn't connected here. The dashboard is opened at `http://[::1]:5173`, because
    another app holds `127.0.0.1:5173`.
18. **The live runs' "default model"** is the current configuration, version 1, GPT-6 Luna at low
    effort, with `MODEL_NAME` unset.

## Phase 1: Tools from the Gateway

The claim agent's tools and the graph's payment go through either route. The model's tools take no id
it could get wrong, and a payment the Gateway refuses waits for a handler.

- [ ] With `UNDERCOVER_GATEWAY_URL` unset, the model's tools are `get_claim`, `get_policy`,
      `get_claim_history`, `list_sections`, `read_section`, `calculate_payout` and `record_decision`.
      `get_claim`, `get_policy`, `get_claim_history` and `calculate_payout` take no claim, policy or
      customer id, and `record_decision` takes no claim id (test on the tools' input schemas).
- [ ] With it set to the stand-in Gateway, the model gets the same tools with the same names and inputs
      (test compares the two schemas). Each model tool call and the graph's payment reach the stand-in
      as `undercover-app___<operation>`, each request signed with `AWS4-HMAC-SHA256` for
      `bedrock-agentcore` in the configured region (test reads the stand-in's headers).
- [ ] On the Gateway route, the bookkeeping never reaches the stand-in: starting the claim, its steps,
      model calls, work times, the security check's read and referral, and the run's calls go to the app
      with the key (test lists the stand-in's calls).
- [ ] `get_claim` gives the model the same text on both routes, and it is the text the check reads (test).
- [ ] A stub model decides a claim on each route with the same decision and payout (tests).
- [ ] An app refusal (a second `record_decision` on a decided claim) reaches the model as a refusal with
      the app's reason on each route, never as a success (tests).
- [ ] A Gateway refusal of a tool is recorded on the claim as the Gateway's, the model gets it as a
      refusal, and the claim goes on to a decision (test).
- [ ] With the approval limit raised to $2,000 and an approval of $1,500: the stand-in refuses the
      payment, the refusal is recorded, and the claim waits for sign-off with `held_by_gateway` set. Dana's
      sign-off pays it once, and the resume finishes with no call to the payment (test).
- [ ] `hold_for_sign_off` answers 401 without the key and with only a handler's sign-in. It is in the key
      group of the access test, and not in the agent's API document. It refuses a declined claim, a paid
      claim, and a claim already waiting (tests).
- [ ] The sign-off panel says the Gateway refused the payment when `held_by_gateway` is set, and that
      the payout is over the approval limit otherwise (vitest).
- [ ] `UNDERCOVER_GATEWAY_URL` and `UNDERCOVER_API` are read when a graph or a client is built: a test
      sets them after the agent is imported and finds them used.
- [ ] After a call through the Gateway route on the main thread, the app's Lambda handler still answers
      `/api/health` in the same process (test). `conftest.py` notes why.
- [ ] No test reads `~/.aws` or reaches AWS: `conftest.py` sets fake keys, a region and missing config
      files, and clears `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` and `UNDERCOVER_MEMORY_ID`.
- [ ] `calculate_payout` is `POST /api/claims/{claim_id}/payout` and takes the policy from the claim.
      The payout tests pass on it, and an unknown claim gets a 404 (tests).
- [ ] The claim agent's instructions say the tools already know the claim, its policy and its customer.
      The commit's pre-commit hook passes.

## Phase 2: The context layer

Data governance's seven files are served as an MCP server, and the app seeds its plans and documents
from them.

- [ ] `context/server.py` reads the seven files once, when it is imported, and splits each document at
      `^## (\d+)\. (.+)$` into sections tagged with their document, number and title. The front matter is
      the source and version, never part of a section (test on the sections).
- [ ] Its tools each have a one-line docstring and no optional inputs, and are marked read-only:
      `lookup_term`, `describe_fields`, `get_plan`, `get_claim_rules`, `search_reference_data`,
      `search_documents`, `read_section`, and `list_sections` if decision A is option 1 (test lists them
      with their annotations).
- [ ] Tests call each tool against the real files:
  - a term in any case;
  - an unknown term, which gets the glossary's terms;
  - each record's fields;
  - each plan, with its deductible benefits and the add-ons;
  - the claim rules;
  - a reference search that needs every word ("lisbon STRIKE"), with its topic;
  - a document search naming each section's file;
  - a section that exists, and one that doesn't.
- [ ] Every answer names its file with that file's system, owner and review date (test across the tools).
- [ ] `context.server.handler`, called with Function URL events with a Function URL's host, lists the
      tools and then calls one, twice in one process, and both succeed (test).
- [ ] `uv run -m context` serves it at `http://127.0.0.1:8006/mcp`, and `npm run dev` starts it beside
      the backend, the agent's service and the dashboard.
- [ ] `npm run check:context` runs
      `uv run --isolated --only-group context python -c "import context.server"` and passes, inside
      `npm run check`. Ruff, pyright, pytest and coverage include `context/`.
- [ ] Seeding loads the plans from `context/plans.yaml` into a `plans` table, which the payout rules read,
      and the two documents into a `documents` table with their versions (2026.1 and 2026.2) from their
      front matter (tests). The payout tests' results are unchanged.
- [ ] The app reads no plan or document from `data/`, and reads `context/` only while seeding. The Phase
      4 test of the backend's file paths from `docs/PLAN_SECURE.md` allows `context/` in seeding only
      (test). `data/plans.json` and the two documents in `data/` are unchanged.
- [ ] The app's `list_sections` and `read_section` routes are gone. The agent's API document has exactly
      `get_claim`, `get_policy`, `get_claim_history`, `calculate_payout`, `record_decision` and
      `pay_claim` (test).
- [ ] The claim agent's context tools work on both routes: on the direct route to a context server the
      tests start, and on the Gateway route as `undercover-context___<tool>` through the stand-in (tests).
      A refusal from the context layer reaches the model as a refusal (test).
- [ ] The commit's pre-commit hook passes.

## Phase 3: A complete decision record

Every decision holds what claims manual section 14 asks for, and the app refuses one that doesn't.

- [ ] The app keeps the latest calculation from `calculate_payout` for the claim's current attempt. A
      new attempt or a release clears it (tests).
- [ ] `record_decision` takes `decided_by` besides the decision, the payout, the section and the reason.
      The agent fills it in and the model never sees it (test on the tool's schema).
- [ ] Each refusal is a 422 naming section 14, with the same answer on any claim, even one that doesn't
      exist (one parametrized test):
  - a blank reason, section or maker;
  - an approval that pays nothing;
  - a decline or a referral that pays something.
- [ ] An approval with no calculation on file for this attempt, or a payout that isn't the calculation's,
      is refused with a 422 naming section 14 (tests).
- [ ] A complete approval holds its calculation's lines and payout. A decline and a referral hold no
      calculation. Each holds `decided_by`, `decided_at` and `manual_version` 2026.2, and the claim view
      shows them (tests).
- [ ] A referral by the security check is recorded with `decided_by` "Security check" (test).
- [ ] The agent's records name the version: "UnderCover Agent, version 1" in a dispatch, and the run's
      version in an evaluation (tests).
- [ ] One shared helper in each suite records a matching calculation first and sends the maker:
      `backend/tests` (it may write the calculation directly), `agent/tests` and `e2e/agent.ts`. The
      e2e helper uses the brief's recipe: one `expense` line under `emergency_medical` for the payout plus
      the plan's deductible, found by a first calculation of the payout alone. No test's expected payout
      changes.
- [ ] The commit's pre-commit hook passes.

## Phase 4: Memory of each customer

With `UNDERCOVER_MEMORY_ID` set, the claim graph saves each decided claim to AgentCore Memory, and the
model can search what it remembers about this customer.

- [ ] `langgraph-checkpoint-aws` is in the dependencies. With `UNDERCOVER_MEMORY_ID` set, the graph's
      store is `AgentCoreMemoryStore` with that id, in the region from `boto3.Session()`, read when the
      graph is built (test on the store's arguments). The checkpoints stay in Postgres.
- [ ] After a decision the agent recorded, the stand-in store gets two events under
      `(customer, claim)`. The customer's message is the summary, the benefit, the incident's date and
      place, and the items. The agent's message is the decision, the payout, the section and the reason.
      Neither holds the story or the documents (test).
- [ ] Work carried on after a restart past `remember` saves nothing twice (test with a stopped graph).
- [ ] Nothing is saved for a claim the check referred, or for an evaluation's copy (tests).
- [ ] `search_memory(query)` searches only under `("customers", <this claim's customer>)`, returns each
      memory's text, and leaves out the summary of the claim being worked (tests).
- [ ] With memory off, or on an evaluation's copy, the model has no `search_memory` tool (tests).
- [ ] When the stand-in store raises a botocore error, saving logs a warning and the claim keeps its
      decision, and a search gives the model the error as a refusal (tests).
- [ ] `backend/instructions/claim.md` names, as goals, what the agent has to go on: the context layer's
      definitions, plans, rules, reference data and documents, and its memory of the customer. The
      claim history still counts for repeat claims (manual section 9). The improver's sentence in
      `agent/improver.py` names the claim agent's new tools.
- [ ] The commit's pre-commit hook passes.

## Phase 5: Every step on the claim page

The claim page shows each step of the agent's work as it happens, where each lookup came from, and the
whole decision record.

- [ ] The app, `agent/main.py` and `dashboard/src/api.ts` know the kinds `lookup`, `memory_search`,
      `memory_save` and `refusal` beside the others. `add_step` refuses any other kind (tests).
- [ ] Each tool call's step names the tool as the Gateway names it on that route
      (`undercover-context___lookup_term`), and by its own name otherwise (tests).
- [ ] A lookup's step says what was looked up and its files with their sources, for each context tool:
      a term, a plan, the claim rules, a record's fields, a reference search, a document search and a
      section read (tests).
- [ ] A memory search's step holds what it found, and a save's step what it saved (tests).
- [ ] Refusals have a step of their own, and the Gateway's are labeled as the Gateway's (tests).
- [ ] On the claim page (vitest):
  - each kind of step shows with its own label, and refusals stand out in Raspberry;
  - a Context used panel, in place of Sections read, lists each lookup once, grouped by file, with each
    file's system, owner and review date;
  - the decision panel shows the whole record: the decision, the payout, the calculation line by line,
    the section, the reason, who decided and when, the manual's version, and who signed off and when.
- [ ] The page refreshes the steps while the claim is in progress, as it does today. The words follow the
      dashboard's plain style and `docs/BRAND.md`.
- [ ] The e2e claim spec sees the decision record with its calculation and the Context used panel, in a run of
      `npx playwright test e2e/claim.spec.ts`. The commit's pre-commit hook passes.

## Phase 6: The live checks and the documents

After one `npm run seed`, with `npm run dev` running (local Postgres, local Supabase and the context
layer), on the default model, on the direct route and with memory off.

- [ ] First, `uv run -m agent CLM-1001` decides CLM-1001 as before, with a complete record, its
      calculation and "UnderCover Agent, version 1", and its tool calls to the context layer answer
      (checked in `undercover.claims` with `docker exec undercover-postgres psql`).
- [ ] `uv run -m agent CLM-1005`, then `uv run -m agent CLM-1006`. Report each decision, and whether the
      agent used the context layer and the claim history for Oliver Grant's second claim.
- [ ] A dispatch at N=25 from the queue page works the rest of the queue as before, and refers CLM-1901
      with the check's reason and "Security check". Report each claim's decision against the security
      build's dispatch, and any section 14 refusal and what the agent did next.
- [ ] An evaluation of the evaluation set's five tiny claims from the Scoreboard page. Report its scores.
- [ ] Signed in as Dana Whitfield, through the Chrome DevTools MCP server: CLM-1005's and CLM-1006's pages
      show the tool calls, the lookups with where each came from, the Context used panel, and the decision
      with its calculation. Screenshots in light and dark mode.
- [ ] With `UNDERCOVER_MEMORY_ID` and `UNDERCOVER_GATEWAY_URL` unset, no memory tool and no Gateway call
      appear on any claim.
- [ ] What the live runs spent, from the costs in the database.
- [ ] `docs/SPEC.md` (the context layer, the memory, the complete record, the Gateway's refusal of a
      payment), `docs/TECH_STACK.md` (with the Deployment section the brief asks for), `docs/PROCESS.md`
      (the context layer in `npm run dev`, `check:context`), `docs/CODE_STANDARDS.md` (one module for each
      route, the context layer, the memory), `docs/STATUS.md` and `data/README.md` (the plans and the two
      documents now come from `context/`) describe the product as it now is. `npm run check` is green with
      `.env` moved away.

## Phase 7: The Gateway, the Policy and Memory on AWS, with RDS and Cognito

The deployed app runs on Amazon RDS for PostgreSQL and signs handlers in with Amazon Cognito, in place of
a hosted Supabase project. The laptop keeps local Postgres and local Supabase Auth, so the deployed app has
its own copy of the data. Every AWS resource is in us-east-1, named `undercover...` and tagged both
`project=undercover` and `label=FDE-lab`. Where a service also takes a description, it starts `FDE-lab:`.

Estimated cost while it exists, from AWS's price list as I remember it, not checked today: a
`db.t4g.micro` instance about $0.016 an hour, 20 GB of gp3 storage about $0.08 a day, and its public IPv4
address $0.005 an hour, so roughly $0.60 a day (the RDS free tier may cover the instance on a new account).
Cognito is free at three users. `uv run monitor.py` shows the real spend.

**Sign-in that works with either provider** (built and tested locally first, with stand-ins):

- [ ] The backend checks a handler's token against the keys at `AUTH_JWKS_URL`, with the issuer and
      audience in `AUTH_ISSUER` and `AUTH_AUDIENCE`. With these unset it uses local Supabase's, as today.
      Tests sign tokens as Supabase's and as Cognito's ID tokens (`token_use` `id`, `aud` the app client
      id) with a stand-in key, and the handler is still found by the token's email.
- [ ] The dashboard signs in through Cognito when `VITE_COGNITO_USER_POOL_ID` and `VITE_COGNITO_CLIENT_ID`
      are set at build time (`InitiateAuth` with `USER_PASSWORD_AUTH`, by `fetch`, no new SDK), and
      through Supabase otherwise. Vitest covers both; the e2e specs still sign in through the Supabase
      stand-in. The bundle stays under Vite's 500 kB warning.

**On AWS** (only after the above is committed). Every AWS component in this phase is created, checked and
deleted by code in notebook cells, not by the build agent's own commands. The cells go in
`labs/week3/week3day3.ipynb`, added beside the lab's own cells under headings that start
`AWS without Supabase:` with an italic line *Only for RDS and Cognito in place of Supabase*, as
`docs/LOCAL_LABS.md` adds cells (the lab's own cells stay as Ed wrote them). The cells use boto3 on the
`[default]` profile, except the one that widens the course policy, which uses `AWS_ADMIN_PROFILE`. Each
cell is safe to run again, and each create cell has a matching delete cell in the teardown. The student
runs the cells, or asks for them to be run; each box below is ticked from the cell's output.

- [ ] The new cells are in the notebook, `ruff check` passes on their code, and the lab's own cells are
      unchanged (`git diff` shows only added cells).

- [ ] The course policy gains `rds:*`, `cognito-idp:*` and the EC2 security-group actions it needs, on
      resources named `undercover...` where AWS allows it, applied with the `<AWS_ADMIN_PROFILE>` administrator
      profile. `aws sts get-caller-identity` then shows `undercover-course`, and it can describe RDS.
- [ ] RDS for PostgreSQL 17, `db.t4g.micro`, 20 GB gp3, single-AZ, no Multi-AZ, backups 1 day, named
      `undercover-db`, with a random master password kept only in `.env` as `AWS_DATABASE_URL`. It is
      publicly accessible, because Lambda outside a VPC can't reach a private one without a NAT gateway
      (about $32 a month); its security group opens port 5432, and the connection requires TLS.
- [ ] `npm run seed` with `DATABASE_URL` set to `AWS_DATABASE_URL` seeds it once; the claims count 26.
- [ ] A Cognito user pool `undercover-handlers` with self sign-up off, an app client with
      `USER_PASSWORD_AUTH` and no secret, and the three handlers added with `HANDLER_PASSWORD`. Each can
      sign in.
- [ ] The app and the context layer are on Lambda with Function URLs, as `labs/week3/week3day2_lab2.ipynb`
      and `labs/week3/week3day3.ipynb` build them, with `DATABASE_URL` on RDS (port 5432, `NullPool`, no
      prepared statements) and the `AUTH_*` settings for Cognito. `/api/health` answers on each, and Dana
      signs in to the deployed dashboard and sees 26 claims.
- [ ] The Gateway has two targets, `undercover-app` and `undercover-context`, with the app's key in
      AgentCore Identity, and lists the agent's tools as `undercover-app___*` and `undercover-context___*`.
- [ ] With the policy engine in `ENFORCE`, a `pay_claim` over $1,000 through the Gateway is refused by the
      Policy and never reaches the app; one within the limit reaches it.
- [ ] One claim worked on the Gateway route on GPT-6 Luna is decided as on the direct route, and its claim
      page shows where each lookup came from.
- [ ] With memory on, a claim is decided and its events saved; about 90 seconds later `search_memory` on a
      second claim for the same customer finds the first.

**Deletion when the lab is finished** (marked to do; teardown cells at the end of the notebook):

- [ ] The Gateway, its targets, the policy engine, the key provider and the Memory are deleted, as the
      lab's Part 6.
- [ ] The RDS instance is deleted with no final snapshot and its automated backups, then its security
      group; the Cognito user pool is deleted; both Lambda functions, their roles and log groups are
      deleted. `AWS_DATABASE_URL` and the Cognito settings are removed from `.env`.
- [ ] A listing by the `label=FDE-lab` tag (Resource Groups Tagging API) and by the `undercover` name shows nothing left, and
      `uv run monitor.py` shows the spend, which is reported.

## Notes from the build
