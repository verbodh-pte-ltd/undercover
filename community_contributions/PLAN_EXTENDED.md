# Plan: the claims pilot, extended

The pilot from `docs/SPEC.md`, planned by the rules in `docs/PROCESS.md` and `docs/PROCESS_EXTENDED.md`.
A backend that holds the claims queue and enforces the payment rule, the UnderCover Agent that decides
one claim at a time, and a dashboard where handlers watch the queue, read each decision, watch the agent
work and sign off large payouts.

## Choices for you

None of these are decided by the documents in `docs/`. Each can be changed before the build starts.

- **The agent has no sign-off tool.** The $1,000 rule lives in the payment route, so a mistaken or
  misled agent still can't pay a large claim.
- **The agent sees claim history only.** It gets the customer's earlier claims, not their address, phone
  or date of birth, because it doesn't need them to decide.
- **The agent starts from the command line** (`npm run agent`), not a dashboard button. The spec doesn't
  ask for a button, and this keeps the backend from calling the agent.
- **SQLite through Python's standard library**, seeded from `data/` on first start. It needs no new
  package. Deleting the database file resets the data.
- **The agent's instructions live in the backend** (`backend/instructions.md`), so the agent and the
  dashboard read the same text through the API.
- **The dashboard checks for new steps every two seconds.** Simpler than a live connection and fast enough
  to watch.
- **End-to-end tests use their own database and ports** (backend 8001, dashboard 5174), so they never
  change the data you see in `npm run dev`.
- **A payment must match the approved payout and is paid once.** This blocks splitting a large payout into
  small ones.
- **Browser checks use the Playwright MCP server.** If it isn't connected, the Chrome DevTools MCP server
  is used instead.

## Claim states

- `waiting`: every claim starts here (`open` in `data/claims.json`)
- `in_progress`: the agent has started the claim
- `waiting_for_sign_off`: the agent approved a payout over $1,000
- `decided`: approved for $1,000 or less, declined, referred, or signed off by a handler

Payment is tracked apart from the state: `paid_amount` stays empty until a payment goes through.

## API

Every route has an `operation_id`, a one-line docstring and no optional inputs.

| Operation | Route | Used by |
|---|---|---|
| `list_claims` | `GET /api/claims` | dashboard |
| `get_claim` | `GET /api/claims/{claim_id}` | agent, dashboard |
| `get_policy` | `GET /api/policies/{policy_id}` | agent, dashboard |
| `get_claim_history` | `GET /api/customers/{customer_id}/claims` | agent, dashboard |
| `get_policy_wording` | `GET /api/policy-wording` | agent |
| `get_instructions` | `GET /api/instructions` | agent, dashboard |
| `list_handlers` | `GET /api/handlers` | dashboard |
| `start_claim` | `POST /api/claims/{claim_id}/start` | agent runner |
| `add_step` / `list_steps` | `POST` / `GET /api/claims/{claim_id}/steps` | agent runner / dashboard |
| `record_decision` | `POST /api/claims/{claim_id}/decision` | agent |
| `pay_claim` | `POST /api/claims/{claim_id}/payment` (takes `amount`) | agent |
| `sign_off` | `POST /api/claims/{claim_id}/sign-off` (takes `handler_id`) | dashboard |

## Phase 1: the store and the read API

`backend/store.py` creates the tables and seeds them from `data/` when empty. The path is `UNDERCOVER_DB`,
defaulting to `backend/undercover.db`. Tests use a fresh database in `tmp_path`.

- [ ] A fresh database has the 25 claims from `data/claims.json`, all `waiting`, and nothing from
      `data/evals/` or `data/security/`
- [ ] `GET /api/claims` returns 25 items with id, summary, type, amount, state and customer name
- [ ] `GET /api/claims/CLM-1001` returns the story, three items and four documents
- [ ] `GET /api/policies/POL-2001` returns the Premium policy for the Rome trip
- [ ] `GET /api/customers/CUS-3001/claims` returns CLM-0412 and no email, phone, address or date of birth
- [ ] `GET /api/policy-wording` and `GET /api/handlers` return the wording in full and the three handlers
- [ ] An unknown claim, policy or customer returns 404; opening the store twice doesn't duplicate rows
- [ ] `npm run check:python` passes

## Phase 2: decisions and the payment rule

`record_decision`, `pay_claim` and `sign_off`. The limit comes from `payout_approval_limit` in
`data/company.json`.

- [ ] `record_decision` takes `decision`, `payout`, `reason` and `policy_section`; a payout above zero on a
      decline or refer returns 422
- [ ] Approving $420.00 sets `decided`; approving $1,250.00 sets `waiting_for_sign_off`
- [ ] `pay_claim` of $420.00 on the $420.00 claim succeeds and sets `paid_amount`
- [ ] **The rule:** `pay_claim` on the $1,250.00 claim before sign-off returns 403 for $1,250.00, $1,000.00
      and $250.00, and nothing is paid
- [ ] A wrong amount returns 422; paying twice, or paying a declined or referred claim, returns 409
- [ ] `sign_off` with HND-01 pays $1,250.00 and sets `decided`; on a claim not waiting it returns 409; an
      unknown handler returns 404
- [ ] `curl -X POST localhost:8000/api/claims/CLM-1001/payment -H "Content-Type: application/json" -d "{\"amount\": 420}"`
      against a running backend returns 409 while the claim has no decision
- [ ] `npm run check:python` passes with coverage at or above 80%

## Phase 3: claim progress and steps

`start_claim`, `add_step`, `list_steps` and `get_instructions`.

- [ ] `start_claim` moves `waiting` or `in_progress` to `in_progress` and clears old steps
- [ ] `start_claim` on a `decided` claim returns 409
- [ ] `add_step` then `list_steps` returns the steps in the order they were added
- [ ] `GET /api/instructions` returns the text of `backend/instructions.md`
- [ ] The instructions say to decide by the policy wording alone, work out the payout by section 10, give
      two or three sentences of reasons with the section, then pay if approving
- [ ] `npm run check:python` passes

## Phase 4: the UnderCover Agent

Six tools named after their operations, calling the backend over HTTP. `run(claim_id, thread_id, llm=None)`
starts the claim, reads the instructions from the API, streams the agent and posts each step.
`uv run -m agent` works every waiting claim in turn, or the ids given; `npm run agent` runs it.

- [ ] Each tool test calls the real app through `TestClient` and gets the right data back
- [ ] A stub-model run on CLM-1001 (read claim, policy, history, wording; approve $420.00; pay) ends
      `decided`, `paid_amount` 420.0, with at least six steps
- [ ] A stub-model run that approves $1,250.00 and calls `pay_claim` ends `waiting_for_sign_off`, nothing
      paid, and the refusal reaches the model as text without crashing the run
- [ ] The runner, with a stub `run`, works only the waiting claims, in queue order
- [ ] `test_model_name` still passes: `openai/gpt-6-luna` by default, `MODEL_NAME` when set
- [ ] No file in `agent/` reads `data/` (a test checks the source)
- [ ] With the backend running and the key in `.env`, `uv run -m agent CLM-1001` records a decision, reasons
      and section (one real model call; free with `MODEL_NAME=openrouter/free`)
- [ ] `npm run check:python` passes

## Phase 5: the dashboard, read only

Hash routes with no router library: the queue (`#/`), a claim (`#/claims/CLM-1001`) and the instructions
(`#/instructions`). Brand from `docs/BRAND.md`. E2E servers move to ports 8001 and 5174 with
`UNDERCOVER_DB=e2e/e2e.db`, deleted before each run by an `.mjs` global setup.

- [ ] Vitest tests with a mocked `fetch` cover the queue, the claim view and the instructions view
- [ ] The queue shows 25 claims with id, customer, type, amount and state, and a count per state
- [ ] The claim view shows story, items, documents, policy, history and, once decided, the decision,
      payout, reasons and section
- [ ] The instructions view shows the text with no way to edit it
- [ ] In the browser (Playwright MCP, or Chrome DevTools MCP if it isn't connected): the queue loads,
      CLM-1001 opens, the back link returns; checked in light and dark mode
- [ ] An e2e test opens the queue, sees 25 claims and opens CLM-1001
- [ ] `npm run check:ts` and `npm run e2e` pass with coverage at or above 80%

## Phase 6: live steps and sign-off

While a claim is `in_progress`, the claim view checks for new steps every two seconds. The queue checks
every five seconds. A claim waiting for sign-off shows a handler picker and a sign-off button.

- [ ] Vitest: steps appear as polling returns more; polling stops once the claim leaves `in_progress`
- [ ] Vitest: signing off posts the chosen handler and shows the claim decided and paid
- [ ] E2E, through the API with no model: start a claim, add steps, and see them appear without a reload
- [ ] E2E: record an approve over $1,000, sign off as Dana Whitfield, and see "Paid $1,250.00"
- [ ] In the browser: `uv run -m agent CLM-1001` with the claim open shows steps arriving, then the decision
- [ ] `npm run check:ts` and `npm run e2e` pass

## Phase 7: the whole queue and hand-over

- [ ] Delete `backend/undercover.db`, run `npm run dev` and `npm run agent`, and watch the queue move in
      the browser
- [ ] Every claim ends `decided` or `waiting_for_sign_off`, and no approved claim over $1,000 is paid
      before sign-off (checked with `GET /api/claims`)
- [ ] `docs/STATUS.md` says what is built, how to run it, how to reset the data and how to run the agent
- [ ] `npm run check` passes in full

## Not in the pilot

- Scoring the agent against `data/evals/`. The spec keeps the answer key for checking later.
- The claims manual, sign-in, real payments and messages to customers, as the spec says.
- Starting the agent from the dashboard.
