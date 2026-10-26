# Plan: the claims pilot

Builds what `docs/SPEC.md` asks for: a backend that holds the claims queue, the UnderCover Agent that decides claims through the backend's API, and a dashboard for the handlers. Six phases, each building on the last.

## Approach

- **The backend owns the rules.** The $1,000 sign-off limit is enforced in the payment operation, not in the agent's instructions, so a payment is blocked even if the agent tries to pay.
- **The agent only talks HTTP.** Its tools call the backend's API. It never reads `data/` directly, so it cannot reach `data/evals/`.
- **The backend seeds from an explicit list of files** in `data/` and never opens `data/evals/` or `data/security/`. A test proves it.
- **Steps are recorded by the agent runner**, not by the model. The runner streams the agent and posts each tool call and result to the backend, so the dashboard can show progress without trusting the model to report it.
- **Plain choices:** Python's built-in `sqlite3` (no ORM), hash-based routing in the dashboard (no router library), and polling for live steps (no websockets).

## Claim statuses

| Status | Meaning | Set when |
|---|---|---|
| `waiting` | In the queue, not started | Seeded (the data's `open`) |
| `in_progress` | The agent is working on it | The agent run starts |
| `awaiting_signoff` | Approved with a payout over $1,000 | The agent records that decision |
| `decided` | Approved and paid, declined or referred | Decision recorded, or a handler signs off |

## API

Every operation has an `operation_id`, a one-line docstring and no optional inputs.

| Method and path | `operation_id` | Used by |
|---|---|---|
| `GET /api/claims` | `list_claims` | dashboard |
| `GET /api/claims/{claim_id}` | `get_claim` | agent, dashboard |
| `GET /api/policies/{policy_id}` | `get_policy` | agent, dashboard |
| `GET /api/customers/{customer_id}` | `get_customer` | agent, dashboard |
| `GET /api/plans` | `list_plans` | agent |
| `GET /api/policy-wording` | `get_policy_wording` | agent |
| `GET /api/instructions` | `get_instructions` | agent, dashboard |
| `POST /api/claims/{claim_id}/decision` | `record_decision` | agent |
| `POST /api/claims/{claim_id}/payment` | `pay_claim` | agent |
| `POST /api/claims/{claim_id}/sign-off` | `sign_off_claim` | dashboard |
| `GET /api/claims/{claim_id}/steps` | `list_steps` | dashboard |
| `POST /api/claims/{claim_id}/steps` | `add_step` | agent runner |
| `POST /api/claims/{claim_id}/start` | `start_claim` | agent runner |
| `POST /api/claims/{claim_id}/run` | `run_agent` | dashboard |
| `GET /api/handlers` | `list_handlers` | dashboard |

A decision is `{decision: approve | decline | refer, payout, reason, policy_section}`. Payout is `0` for decline and refer.

---

## Phase 1: Backend data and read operations

Builds the SQLite store, seeds it from `data/`, and serves the records the agent and dashboard read.

- `backend/db.py`: connection, schema (claims, policies, customers, plans, handlers, decisions, payments, steps). Claims, policies and customers keep their JSON as a column with the fields we filter on alongside. The database file is `backend/undercover.db` (git-ignored), overridable with `UNDERCOVER_DB` for tests.
- `backend/seed.py`: loads `claims.json`, `policies.json`, `customers.json`, `plans.json`, `handlers.json`, `company.json` and `policy_wording.md` on startup when the database is empty. Maps status `open` to `waiting`.
- `backend/instructions.md`: the agent's instructions, served read only.
- Read routes from the API table: `list_claims`, `get_claim`, `get_policy`, `get_customer`, `list_plans`, `list_handlers`, `get_policy_wording`, `get_instructions`.

**Success criteria**
- [x] `uv run pytest backend` passes with tests for each read route.
- [x] `GET /api/claims` returns 25 claims, all `waiting`.
- [x] `GET /api/claims/CLM-1001` returns the claim with its story, items and documents.
- [x] `GET /api/claims/CLM-9999` returns 404.
- [x] `GET /api/policy-wording` returns the wording starting `# UnderCover Travel Insurance: Policy Wording`.
- [x] A test proves the seed never opens a path under `data/evals/` or `data/security/`, and no route returns a claim from either.
- [x] Every route in `app.openapi()` has an `operation_id` and a summary, checked by a test.
- [x] `npm run check:python` passes.

## Phase 2: Decisions, payments and the sign-off rule

Builds the write operations and the $1,000 rule.

- `record_decision`: stores the decision. Approve over $1,000 sets `awaiting_signoff`; anything else sets `decided`. Rejects a second decision (409) and a payout on decline or refer (422).
- `pay_claim` takes `{amount}`. Allowed only on an approved claim, for the approved payout, once. Over $1,000 without a handler's sign-off it returns 403 with the reason, and records nothing.
- `sign_off_claim` takes `{handler_id}`, records the sign-off, makes the payment and sets `decided`. Only on `awaiting_signoff` claims, by a handler with `can_approve_over_limit`.
- `add_step` and `list_steps`: an ordered log of `{at, kind, text}` per claim.
- `get_claim` now includes the decision, the payment, the sign-off and the latest status.

**Success criteria**
- [x] Tests cover: approve under the limit then pay succeeds; approve over the limit sets `awaiting_signoff`; paying over the limit before sign-off returns 403 and no payment is stored; sign-off pays and sets `decided`; double decision returns 409; paying the wrong amount returns 422; paying twice returns 409.
- [x] With `curl`, `POST /api/claims/CLM-1001/payment` with `{"amount": 1500}` after an approve of $1,500 returns 403.
- [x] `npm run check:python` passes with coverage of 80% or more.

## Phase 3: The UnderCover Agent

Replaces the hello-world agent with one that decides claims.

- `agent/tools.py`: `get_claim`, `get_policy`, `get_customer`, `list_plans`, `get_policy_wording`, `record_decision`, `pay_claim`, each a thin call to the backend. HTTP errors come back to the model as text (such as the 403 for sign-off) so it can carry on.
- `agent/main.py`: `run(claim_id, thread_id)` loads its instructions from `get_instructions`, sets the claim `in_progress`, streams the agent and posts each tool call and result as a step. Model choice stays as it is (`MODEL_NAME` or `openai/gpt-6-luna` with low reasoning).
- `backend/instructions.md`: read the claim, policy, customer history, plans and wording; check cover, dates, waiting periods, limits, exclusions and the deductible; decide; record the decision with a two or three sentence reason and the policy section; pay if approved.
- `run_agent` route: starts `agent.run` in a FastAPI background task and returns at once.
- `uv run -m agent CLM-1001` runs one claim from the command line.

**Success criteria**
- [x] Tests with a stub model: a scripted run reads the claim, records an approve, pays, and leaves the claim `decided` with steps logged.
- [x] A stubbed run that tries to pay $1,500 gets the 403 message back and the claim stays `awaiting_signoff` with no payment.
- [x] A test proves no agent tool takes a path or reaches anything outside the backend's API.
- [x] Live check (one claim, a few cents, or free with `MODEL_NAME=openrouter/free`): `uv run -m agent CLM-1001` records a decision with a reason and a policy section, visible at `GET /api/claims/CLM-1001`.
- [x] `npm run check:python` passes.

## Phase 4: Dashboard, brand and queue

Builds the look and the first screen.

- Brand from `docs/BRAND.md`: CSS variables for the palette and surfaces, light and dark mode, Archivo, Instrument Sans, Instrument Serif and JetBrains Mono from Google Fonts.
- `api.ts`: typed fetch helpers for each operation the dashboard uses.
- Hash routes: `#/` queue, `#/claims/:id` claim, `#/instructions`.
- Queue page: a table of claims with id, customer, type, amount claimed, submitted date and a status badge, plus a count for each status. Badge colors: waiting Lavender, in progress Cyan, awaiting sign-off Coral Red, decided Mint.

**Success criteria**
- [x] Vitest tests for the queue page and status badges with a mocked fetch.
- [x] In the browser with the Playwright MCP server: the queue shows 25 claims with their status, in light and dark mode, and screenshots match the brand.
- [x] `npm run check:ts` passes with coverage of 80% or more.

## Phase 5: Claim page, live steps, sign-off and instructions

Builds the rest of what the team needs to see.

- Claim page: the story, items, documents, policy and customer history; the agent's decision, payout, reason and policy section; the payment and sign-off.
- A "Run agent" button on `waiting` claims that calls `run_agent`.
- Live steps: while a claim is `in_progress`, the page polls `list_steps` and `get_claim` every two seconds and shows each step as it lands.
- Sign-off: on `awaiting_signoff` claims, pick a handler and sign off, which releases the payment.
- Instructions page: the agent's instructions, read only.

**Success criteria**
- [x] Vitest tests for the claim page, the steps list, the sign-off form and the instructions page.
- [x] In the browser: CLM-1001 shows its story, items and documents; the instructions page shows the instructions with no way to edit them.
- [x] In the browser, with a live run on one claim: the steps appear one by one and the decision shows when it is done.
- [x] In the browser, for a claim the agent approved over $1,000: the page shows "Waiting for sign-off" with no payment, and after sign-off it shows the payment and `decided`.
- [x] `npm run check:ts` passes.

## Phase 6: End-to-end tests and wrap-up

- Playwright tests (no model calls; they seed decisions through the API): the queue lists 25 claims; a claim page shows its details; an over-limit approval is blocked until sign-off, then paid; the instructions page is read only.
- The Playwright backend runs on its own fresh database (`UNDERCOVER_DB=e2e/test.db`, cleared by a small `.mjs` script before start, so it works on Windows) and never reuses the dev server's data.
- Replace `e2e/health.spec.ts` with these.
- Update `docs/STATUS.md` with what is built and how to run it. The `*.db` rule in `.gitignore` already covers `backend/undercover.db` and `e2e/test.db`.

**Success criteria**
- [x] `npm run e2e` passes.
- [x] `npm run check` passes in full.
- [x] `docs/STATUS.md` describes the pilot and how to run it, the agent and the dashboard.
