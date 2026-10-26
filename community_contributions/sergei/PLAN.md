# Plan: the pilot

The pilot builds what `docs/SPEC.md` asks for: a backend that holds the claims, a dashboard where the team watches the queue and signs off big payouts, and the UnderCover Agent, which works a claim from start to decision.

The skeleton already has the checks, the hooks and a hello-world in each of `backend/`, `agent/`, `dashboard/` and `e2e/`. Every phase grows it, and no phase leaves it broken.

For each phase: build it with tests, check every criterion, tick the boxes here, update `docs/STATUS.md`, then make one commit (subject line only).

## Decisions to review

The spec leaves these open. I picked the simplest option that meets it. Tell me if you want any of them different before I build.

1. **The agent is run from the command line, not from the dashboard.** `uv run -m agent.main CLM-1001` works one claim, and with no claim id it works every waiting claim. The dashboard shows the queue, the decisions and the live steps, and it takes the sign-off. A "Run agent" button is not in the spec, and it would tie the backend to the agent and to a model call in the end-to-end tests.
2. **A referred claim is `decided` with the outcome `refer`.** The spec's four stages have no place for "handed to a person", and what a handler then does is outside the pilot. The dashboard shows the outcome next to the stage.
3. **Sign-off pays the claim.** A handler picks their name and clicks "Sign off and pay $X". One operation records the sign-off and makes the payment, so there is no second step to forget.
4. **The payment must equal the approved payout.** The limit is "over $1,000", so exactly $1,000.00 pays without sign-off and $1,000.01 does not. This stops an agent from approving $900 and paying $9,000.
5. **The agent sees only what it needs of a customer.** `get_claim_history` returns the earlier claims and nothing else. Phone, email, address and date of birth are never loaded into the backend, because nothing in the pilot uses them.
6. **The policy comes with its plan's limits.** `get_policy` returns the plan's numbers (per-item limit, deductible and so on) next to the add-ons, so a small model does not have to read them from a table. They are the same numbers as in the wording, which the agent also reads.
7. **The agent's instructions live in `backend/instructions.md`.** The backend serves them read only, and the agent fetches them at the start of every run. To change them, edit the file.
8. **The agent's steps reach the dashboard by polling.** The agent's runner posts each step to the backend, and the dashboard refreshes every 2 seconds. No websockets.
9. **The end-to-end tests run on their own ports and their own database** (backend 8100, dashboard 5273, `e2e/e2e.db`, rebuilt on every run). They change claims, and they must never touch the database you use with `npm run dev`.
10. **Not used:** `claims_manual.md` (spec: not for now), `policy_wording_short.md`, `knowledge.json` (Week 2), `security/` (Week 3) and `evals/`. I will not open `data/evals/` unless you ask. The pilot does not score the agent against the handlers' decisions.

## How the parts fit

```
dashboard (React)  --/api-->  backend (FastAPI + SQLite)  <--/api--  agent (create_agent + tools)
  queue, claim, instructions,       claims, workflow, rules,            run(claim_id, thread_id)
  sign-off                          steps, instructions                 started from the command line
```

The agent never touches a file or the database. Every read and write goes through the backend's API, so the backend is the only place the rules are enforced.

### Claim stages

| Stage | Meaning | Becomes this when |
|---|---|---|
| `waiting` | Nobody has started it | Seeded (`status: open` in the data) |
| `in_progress` | The agent is working it | `start_claim` |
| `awaiting_signoff` | Approved over $1,000, payment on hold | `decide_claim` approves a payout over the limit |
| `decided` | Finished | `decide_claim` declines, refers or approves up to the limit; or a handler signs off |

The outcome (`approve`, `decline`, `refer`), the payout, the reason, the policy section, who signed off and the payment (`paid_amount`, `paid_at`) are fields on the claim. A claim that was approved up to the limit is `decided` as soon as the agent decides, and shows "payment pending" until `pay_claim` runs. Paying is a separate operation, as `docs/TECH_STACK.md` says.

### API

Every operation has an `operation_id`, a one-line docstring and no optional inputs. That is why there are no filters: the dashboard filters in the browser.

| Operation | Route | Input | Does |
|---|---|---|---|
| `health` | `GET /api/health` | none | Exists already |
| `list_claims` | `GET /api/claims` | none | The queue: id, type, customer name, summary, amount claimed, stage, outcome, payout |
| `get_claim` | `GET /api/claims/{claim_id}` | none | The claim in full, with the decision, sign-off and payment |
| `list_steps` | `GET /api/claims/{claim_id}/steps` | none | What the agent did, in order |
| `get_policy` | `GET /api/policies/{policy_id}` | none | Plan and its limits, add-ons, trip, travelers |
| `get_claim_history` | `GET /api/customers/{customer_id}/claim-history` | none | The customer's earlier claims |
| `get_policy_wording` | `GET /api/policy-wording` | none | The wording as text |
| `get_instructions` | `GET /api/instructions` | none | The agent's instructions as text |
| `list_handlers` | `GET /api/handlers` | none | The handlers, with whether each can sign off |
| `start_claim` | `POST /api/claims/{claim_id}/start` | `thread_id` | `waiting` or `in_progress` becomes `in_progress` |
| `add_step` | `POST /api/claims/{claim_id}/steps` | `thread_id`, `kind`, `text` | Records a step |
| `decide_claim` | `POST /api/claims/{claim_id}/decision` | `decision`, `payout`, `reason`, `policy_section` | Records the decision |
| `pay_claim` | `POST /api/claims/{claim_id}/payment` | `amount` | Pays the claim, or is refused |
| `sign_off_claim` | `POST /api/claims/{claim_id}/sign-off` | `handler_id` | A handler releases an over-limit payout, which is paid |

Errors: 404 for an unknown id, 409 when the claim is in the wrong state or the payment is blocked, 403 when a handler may not sign off, 422 for bad input. The message says what to do next, because the agent reads it.

### Data

SQLite at `backend/undercover.db` (already git-ignored), or the path in `UNDERCOVER_DB`. The backend seeds it from `data/` when the file does not exist: `claims`, `policies`, `plans`, `customers` (name and claim history only), `handlers` and the company settings (the $1,000 limit). A `steps` table holds the activity. The wording is read from `data/policy_wording.md`. `uv run -m backend.seed` rebuilds the database from scratch, which gives a fresh queue of 25 waiting claims.

The lint rules are strict (complexity of 10, no magic numbers outside tests, no SQL built from strings, strict hooks and accessibility rules in the dashboard). I will shape the code to fit them and not turn any off.

---

## Phase 1: Records and the read API

**Builds:** the database, the seed from `data/` and every read operation except the instructions: `list_claims`, `get_claim`, `get_policy`, `get_claim_history`, `get_policy_wording`, `list_handlers`. Plain `sqlite3` with a connection per request and Pydantic models for the responses, so there are no new dependencies. It comes first because everything else, the agent included, reads these records.

**Success criteria**

- [x] `uv run -m backend.seed` creates `backend/undercover.db` and prints the counts: 25 claims, 50 policies, 49 customers, 3 plans, 3 handlers. Running it a second time prints the same counts.
- [x] After `npm run dev`, `curl localhost:8000/api/claims` returns 25 claims, all with `"status": "waiting"`, each with a `customer_name`.
- [x] `curl localhost:8000/api/claims/CLM-1001` returns the story, 3 items, 4 documents and `null` for the decision fields.
- [x] `curl localhost:8000/api/policies/POL-2001` returns plan `premium` with `per_item_limit` 300 and `deductible` 50.
- [x] `curl localhost:8000/api/customers/CUS-3001/claim-history` returns the claim `CLM-0412` and no key named `email`, `phone`, `address` or `date_of_birth`.
- [x] `curl localhost:8000/api/policy-wording` returns text that starts with `# UnderCover Travel Insurance: Policy Wording`.
- [x] `curl -i localhost:8000/api/claims/CLM-9999` returns 404.
- [x] A test builds the database in a temporary file, so no test touches `backend/undercover.db`.
- [x] A test walks `app.openapi()` and fails for any operation without an `operationId`, with a multi-line description or with an optional parameter or body field.
- [x] A test fails if any non-test source in `backend/` or `agent/` mentions `evals`, so the answer key stays out of reach.
- [x] `npm run check:python` passes: ruff, pyright and pytest with coverage of 80% or more.

## Phase 2: The claim workflow and the $1,000 rule

**Builds:** `start_claim`, `add_step`, `list_steps`, `decide_claim`, `pay_claim` and `sign_off_claim`, with the stage rules above. The block on payments lives here, in the backend, so it holds whatever the agent does or says. Doing it before the agent means the rule is proven before anything can try to break it.

**Success criteria**

Run these with curl against a freshly seeded database, then again as pytest cases.

- [x] `start_claim` on CLM-1001 returns stage `in_progress`, and `list_claims` shows it. Starting it again is allowed. Starting a decided claim returns 409.
- [x] `add_step` followed by `list_steps` returns the steps in the order they were added, each with a timestamp.
- [x] `decide_claim` on a claim that was not started returns 409. A second decision on the same claim returns 409.
- [x] Approve CLM-1001 for $420.00: the stage is `decided`, `pay_claim` for 420 sets `paid_amount` and `paid_at`, and a second `pay_claim` returns 409.
- [x] Approve CLM-1008 for $2,200.00: the stage is `awaiting_signoff`. `pay_claim` for 2200 returns 409 with a message that mentions the handler's sign-off, and the claim is still unpaid.
- [x] `pay_claim` with an amount other than the approved payout returns 409.
- [x] `sign_off_claim` on CLM-1008 by HND-01 returns stage `decided`, with `signed_off_by` HND-01 and `paid_amount` 2200. A second sign-off returns 409. An unknown handler returns 404. A handler without `can_approve_over_limit` returns 403.
- [x] A payout of exactly $1,000.00 is paid without sign-off. A payout of $1,000.01 is held.
- [x] A decline or a refer with a payout above 0 returns 422, and so does an approve with a payout of 0.
- [x] Declining CLM-1004 and referring CLM-1005 both end at stage `decided`, with the reason and policy section stored.
- [x] The openapi test from Phase 1 still passes with the new operations.
- [x] `npm run check:python` passes.

## Phase 3: The agent

**Builds:** `backend/instructions.md` and `get_instructions`; in `agent/main.py`, the tools `get_claim`, `get_policy`, `get_claim_history`, `get_policy_wording`, `decide_claim` and `pay_claim`, which replace the hello-world tool. `run(claim_id, thread_id)` fetches the instructions from the backend, calls `start_claim`, runs the agent and posts each step as it happens (a tool call, a tool result or a message) with `add_step`. A small command line starts it, with `npm run agent` as the shortcut.

The tools hand a refusal from the backend back to the model as text, and do not crash the run, so the agent can see that a payment was blocked and carry on.

The instructions say, in plain language:

- the goal: decide the claim as a handler would, from the policy wording alone;
- the order of work: read the claim, then its policy, the customer's history and the wording;
- how to check: dates against the covered trip, the benefit and its waiting period, the exclusions and the limits, and the documents the wording asks for;
- the three outcomes: approve when the wording covers it, decline when it does not, refer when the wording does not settle it or a required document is missing;
- how to work out the payout, following section 10 of the wording;
- how to explain: two or three sentences and the section of the wording that decides it;
- how to pay: pay a payout of $1,000 or less with `pay_claim` and the exact amount. Never pay a payout over $1,000. Record the decision, say that a handler's sign-off is needed, and stop.

**Success criteria**

- [x] `curl localhost:8000/api/instructions` returns the instructions, and they mention the $1,000 limit.
- [x] Test with a stub model that approves $420 and pays: the claim ends `decided` and paid, and its steps include a tool call, a tool result and a final message, in order.
- [x] Test with a stub model that approves $2,200 and then tries to pay: the tool returns the sign-off message to the model, the claim stays `awaiting_signoff` and unpaid.
- [x] Tests with a stub model that declines and that refers: the reason and policy section are stored.
- [x] `run` can be called twice for the same claim without an error, so a stuck run can be retried.
- [x] `grep -rn "backend" agent --include=*.py` shows only the URL, the tests and the fixtures, so the agent reaches the backend through its API alone.
- [x] No test calls a model: `grep -rn "OPENROUTER" agent/tests` finds only the existing `model()` test, which builds a client and sends nothing.
- [x] A real run with the model in `OPENROUTER_API_KEY`: with the backend up, `uv run -m agent.main CLM-1001` finishes. `curl .../api/claims/CLM-1001` then shows a decision, a reason and a policy section, and `curl .../api/claims/CLM-1001/steps` shows the work. If the key or the model fails, I stop and tell you what happened.
- [x] `npm run check:python` passes.

## Phase 4: The dashboard

**Builds:** the screens the team sees, read only for now, styled to `docs/BRAND.md`:

- **Queue:** a table of the 25 claims with the stage, outcome and payout, counts for each stage, and a filter by stage. It refreshes every 2 seconds, so rows move on their own as the agent works.
- **Claim:** the claim in full: the customer's story, the items, the documents as text, the policy (plan, add-ons, trip dates), the claim history, the decision with its reason and policy section, and the agent's steps as a live feed with tool results folded away.
- **Instructions:** the agent's instructions as plain text, with nothing to edit.

Structure: `api.ts` with the types and a typed fetch for each operation, a small polling hook, a hash route for the three views (no new router dependency), one component per view, and one stylesheet with the brand colors as variables for light and dark mode. The fonts load from Google Fonts. Stages use Harbor for in progress, Saffron for waiting for sign-off and Lagoon for decided, with a neutral tone for waiting. Ember is kept for errors, and money reads like $1,250.00.

**Success criteria**

- [x] vitest tests with a stubbed `fetch` cover: queue rows and stage labels, the stage filter, the claim page with and without a decision, the step feed, the instructions page, and the money format. Coverage is 80% or more.
- [x] `npm run check:ts` passes: prettier, eslint, `tsc` in strict mode and vitest. `grep -rnE ": any|as any|<any>" dashboard/src` finds nothing.
- [x] Playwright MCP on a freshly seeded database, at `localhost:5173`: the snapshot shows 25 claim rows, all "Waiting", with the stage counts above the table.
- [x] Playwright MCP: the stage filter narrows the table, and opening CLM-1001 shows the story, 3 items, 4 documents, the Premium plan and the claim history.
- [x] Playwright MCP, live: with the claim page open, run `uv run -m agent.main CLM-1001` (or the same calls with curl). The stage goes Waiting, In progress, Decided and the steps appear without a reload. I check the snapshot a few seconds apart.
- [x] Playwright MCP: the Instructions page shows the text and has no textarea, input or button.
- [x] Playwright MCP: screenshots in light and dark mode (`browser_emulate_media`) use the Paper and Night backgrounds, with panels that have no outline. The computed font of the body is Instrument Sans, and identifiers and amounts use JetBrains Mono.
- [x] Playwright MCP: at 1280 px and at 390 px wide the page has no horizontal scroll, and the console has no errors.

## Phase 5: Sign-off, and the end-to-end tests

**Builds:** the sign-off panel on the claim page. For a claim that is `awaiting_signoff` it says the payout is on hold, lists the handlers from `list_handlers`, and has the button "Sign off and pay $X". Afterwards the page shows who signed off, when, and the payment. Then the Playwright tests, set up to run on their own ports and database (decision 9): the config starts the backend on 8100 with `UNDERCOVER_DB=e2e/e2e.db` after a fresh seed, starts the dashboard on 5273 pointing at it, and never reuses a running server. The Vite proxy target reads from an environment variable and keeps 8000 as its default.

In the tests, the browser is the handler and the test's API calls play the agent, so no model is involved.

**Success criteria**

- [x] vitest tests cover the sign-off panel: it appears only for `awaiting_signoff`, the button posts the chosen handler, and the page shows the result.
- [x] `npm run e2e` passes with these tests:
  - [x] the queue lists 25 waiting claims;
  - [x] a claim the "agent" starts, works and approves for $420 shows as Decided with its reason, policy section and "Paid $420.00";
  - [x] a claim approved for $2,200 shows "Waiting for sign-off"; a direct payment through the API is refused with 409; a handler signs off in the browser, and the page then shows the handler's name and "Paid $2,200.00";
  - [x] the Instructions page has no way to edit;
  - [x] the existing health test.
- [x] `npm run e2e` passes twice in a row, because each run starts from a fresh database.
- [x] With `npm run dev` running, `curl localhost:8000/api/claims` still shows 25 waiting claims after `npm run e2e`, so the tests never touched the development database.
- [x] `npm run check` passes.

## Phase 6: A whole run, and the hand-over

**Builds:** no new features. This phase checks the product as a handler would use it, then leaves the repo ready.

**Success criteria**

- [x] Seed a fresh database and start `npm run dev`. Run the real agent, one by one, on a mix of claims and watch each one live in the dashboard through Playwright MCP: CLM-1001 (trip delay, $420), CLM-1002 (baggage, $1,150), CLM-1008 (trip cancellation, $2,440) and CLM-1003 (emergency medical, $3,800). These sit on both sides of the limit. Each ends `decided` or `awaiting_signoff`, with a two or three sentence reason and a policy section.
- [x] Any claim the agent approves over $1,000 is unpaid until I sign it off in the dashboard. Then it shows the handler and the payment.
- [x] I read the four decisions against the wording and tell you what looks wrong. I do not compare them with `data/evals/`, which I leave closed.
- [x] If the run shows the instructions need work, I fix `backend/instructions.md`, re-run the affected claim and say what changed.
- [x] `docs/STATUS.md` says what is built, how to run it (`npm run dev`, `npm run agent`, `uv run -m backend.seed`, `npm run check`), the ports and the databases, and lists the operations.
- [x] `npm run check` passes. I report the results, with the Python and TypeScript coverage figures.
- [x] The git log has one commit per phase, none made with `--no-verify`, and nothing is pushed.
- [x] I leave `backend/undercover.db` freshly seeded, so your first run starts from 25 waiting claims.

---

## Not in the pilot

The claims manual, sign-in, real payments, messages to customers, scoring against `data/evals/`, editing the instructions, a "Run agent" button, and anything from a later day's brief.
