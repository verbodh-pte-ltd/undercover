# Plan: the UnderCover Copilot

The brief is `docs/COPILOT.md`. The Copilot is an agent for our handlers in a sidebar on every page. It
looks things up, starts the agent's work, sends decisions back and signs them off, always as the handler
who asked and with that handler's permissions. It asks before it pays, waits in code for the work it
starts, keeps each conversation and remembers each handler. It runs as a second AgentCore app beside the
claim agent.

**Sidenote from the brief.** For consistency with the course, build the Copilot the way the course has
built its agents. Its agent loop is a LangGraph `StateGraph` written by hand: a node that calls the model,
LangGraph's `ToolNode`, and the loop between them, with a `PostgresSaver` checkpointer, as in
`labs/week2/week2day3.ipynb` and the claim agent's graph. Don't use LangChain's `create_agent` for it. The
pause before a sign-off is an `interrupt()` in a node of its own, resumed with `Command(resume=...)`,
since LangGraph runs an interrupted node again from the top when it resumes, as the claim agent's wait for
a handler is a node of its own. The handler's sign-in reaches the graph and its tools in the graph's
context (`context_schema`, read through `ToolRuntime`), never in the config, whose values LangGraph saves
with each checkpoint. The Copilot is an AgentCore app of its own, made with `BedrockAgentCoreApp`, with one
entrypoint and each turn's work registered as an async task, as the claim agent's AgentCore app does, and
it is synchronous throughout, as the claim agent is. `labs/week3/week3day4_lab1.ipynb` and
`labs/week3/undercoverlab/` show a pause in one Runtime session resumed in another. Its memory is
`AgentCoreMemoryStore` as a LangGraph store, as the claim agent's memory is, with the Memory itself made as
in `labs/week3/week3day3.ipynb`. It reaches the context layer over MCP with `MultiServerMCPClient`, as in
`labs/week2/week2day2.ipynb`, and through the Gateway as in `labs/week3/week3day2_lab2.ipynb` and
`labs/week3/week3day3.ipynb`. `labs/week3/week3day4_lab2.ipynb` deploys the claim agent and points the
app at it, and `labs/week3/week3day5_lab1.ipynb` deploys the Copilot beside it, so match the names that
notebook uses. Read these notebooks without running them. Where a notebook and the brief differ, follow the
brief.

## Before this plan starts

This plan builds on the runtime build (`docs/PLAN_RUNTIME.md`), which is being built now. It needs from
that build: the claim agent as an AgentCore app (`agent/entrypoint.py`) to copy the pattern from; the app's
two routes to an agent (`backend/agent_service.py`); the send back (`send_back_claim`); and the app
creating runs itself, so starting a dispatch or an evaluation returns the run's id. This is an order of
work, not a change to the design: the Copilot is still a second AgentCore app beside the claim agent, in
the same package, as the brief says. Start Phase 1 only once all five runtime phases are ticked.

## Where our build differs from the brief

- **Sign-in is Supabase on the laptop and Cognito on AWS.** `dashboard/src/auth.ts` already chooses, and
  already renews a Cognito token before it expires. Both get renewed when less than 20 minutes are left,
  before every message and every answer to a confirmation. The app checks either the same way
  (`backend/auth.py`), so nothing else changes.
- **Our database is local Postgres on the laptop and RDS on AWS, not Supabase.** That's UnderCover's own
  choice, to keep the claims data inside its AWS account, and the runtime build made the same one. Where
  the brief says "our Supabase database", read the database in `DATABASE_URL`. The Copilot's
  `DATABASE_URL` on Runtime is the RDS database, straight to port 5432 with TLS, as the claim agent's is
  (`labs/week3/week3_aws_route.py`). Prepared statements stay off, so it would work through Supabase's
  pooler as well.
- **Names from `week3day5_lab1.ipynb`:** the runtime `copilot`, started from `runtime/copilot.py` with the
  extra permissions in `runtime/copilot_policy.json`; the Memory `undercover_copilot_memory`; the settings
  `UNDERCOVER_COPILOT_MEMORY_ID`, `UNDERCOVER_COPILOT` and `UNDERCOVER_COPILOT_RUNTIME`; Dana's handler id
  `HND-01` as the Memory's actor.

## Approach

- **Two kinds of saved state, both in the one database.** The conversation itself (its messages and where
  the graph stands, the pause included) is the graph's checkpoints, saved by `PostgresSaver` after every
  step, so any later turn or Runtime session carries it on. What the sidebar shows (each turn's state, its
  steps, a waiting confirmation card, the answer) is the app's own records, written only through the
  app's routes. The sidebar reads only the app.
- **The sign-in only passes through, and is a hard rule at every boundary.** Dashboard to app in the
  `Authorization` header; app to Copilot in the payload; Copilot to app in the `Authorization` header
  again. Inside the Copilot it lives only in the graph's context for the length of one request. It is
  never written to the checkpoints, the config, the steps, the turn records, the card, the memory, an
  error message or a log. A failed turn's reason is the exception's type and message, never a request's
  headers or payload. A test saves a turn, a pause and a failure, then searches all of these for the token.
- **A request with the agent's key is the agent's.** The app's handler check refuses a request that
  carries the key, even with a valid sign-in, so the key can never stand in for a handler or be added to
  one. Every route the Copilot calls is a handler's route.
- **The Copilot's client to the app is its own.** It never uses `backend()`, which sends the agent's key,
  so it can't send the key by mistake. A test fails if any Copilot module imports `backend()` or reads
  `UNDERCOVER_API_KEY`.
- **Figures come from code.** The success-criteria tool works out all three figures from the app's
  records and hands the model a finished table. The confirmation card is filled by the app from its
  records, by claim id. The model only writes around them.
- **Waiting is a tool that loops in code.** `wait_for_run(run_id)` reads the run every 5 seconds, up to 15
  minutes, and makes no model call while it waits.
- **The app owns the turn's state.** The Copilot reports each step, the pause, the end and a failure
  through the app's routes, and the sidebar reads only the app. The app's state is the one the sidebar
  trusts, including after a reload.
- **Names stay short.** `agent/copilot.py` (the graph and tools), `agent/copilot_api.py` (its client to the
  app), `agent/copilot_entrypoint.py` (the AgentCore app), `backend/copilot_service.py` (the app's calls to
  it), `backend/instructions/copilot.md` (its instructions).

## Phase 1: conversations in the app, and the reads the Copilot needs

- **Tables** (in `backend/store.py`, emptied by `npm run seed`): `conversations` (id: 36 to 100 letters,
  digits and hyphens; the handler; the title, from the first question; when); `turns` (conversation, the
  message, state `working`, `waiting`, `done`, `failed` or `stopped`, the answer, model calls, cost,
  seconds, the waiting card, the error, `started_at`, `answered_at`); `copilot_steps` (turn, kind, tool,
  main input, state, seconds, detail). A turn is `working`, `waiting_for_confirmation` (paused with its
  card, until the handler answers), `done`, `failed` or `stopped`. A new message is taken only when the
  conversation's latest turn is `done`, `failed` or `stopped`.
- **The handler check** (`signed_in_handler`) refuses a request that also carries the agent's key, with
  401, as `docs/COPILOT.md` item 2 says.
- **The handler's routes** (all need a signed-in handler, and only the conversation's own handler, 404
  otherwise): `create_conversation`, `list_conversations` (newest first), `get_conversation` (its turns
  and steps, with a turn still working 20 minutes after it started or was answered shown as `stopped`),
  `send_message` (409 while a turn is working; a waiting confirmation must be answered first), and
  `answer_confirmation` (sign off or cancel). The last two record the turn, ask the Copilot with the
  conversation's id as the session, passing the handler's token, and reply to the dashboard at once; a
  Copilot that didn't take the turn marks it failed with the reason.
- **The Copilot's routes** (the handler's sign-in, never the key; only for the handler's own working
  turn): `record_copilot_step`, `pause_turn` (the app fills the card from its records: claim, customer,
  amount, the agent's reason), `finish_turn` and `fail_turn`.
- **New reads for handlers**: `find_customers` by name, `get_customer` (who they are, their policies,
  earlier claims and claims in the queue), `get_run` by id (state and summary or measures), and
  `get_success_counts` (claims in the queue paid over the approval limit, and how many carry a sign-off).
  `start_dispatch` and `start_evaluation` return the run's id. None of them shows the handlers' decisions
  on the labeled sets.
- **`get_copilot_configuration`**: the Copilot's model (GPT-6 Luna at low effort, or `MODEL_NAME`) and its
  instructions.
- **`backend/copilot_service.py`**: posts to `UNDERCOVER_COPILOT` (default `http://127.0.0.1:8081`), or to
  `UNDERCOVER_COPILOT_RUNTIME` with the conversation's id as the session id, retrying a conflict up to 3
  times over a few seconds. Taken, refused or unknown, as the claim agent's route.

Done when:

- [ ] Tests: a conversation's routes refuse another handler (404) and a request with only the agent's key
      (401)
- [ ] Every handler's route refuses a valid sign-in sent with the agent's key (401); the existing
      dashboard tests still pass
- [ ] One turn at a time (409); a message refused while a confirmation waits
- [ ] A turn shown as stopped after 20 minutes working; a waiting turn never is
- [ ] The card is filled from the records, whatever the Copilot sends
- [ ] `find_customers`, `get_customer`, `get_run` and `get_success_counts` return what the brief lists, with
      no labeled decisions
- [ ] `copilot_service` sends the session id and retries a conflict; a Copilot that didn't take a turn
      marks it failed
- [ ] `npm run seed` empties the conversations
- [ ] `npm run check:python` passes; committed

## Phase 2: the Copilot's graph and tools

- **The gate, before anything else.** `start_turn` and `answer_confirmation` in `agent/copilot.py` each
  begin by asking the app who is signed in (`get_signed_in_handler`) with the turn's token. If the app
  refuses, the turn fails with that reason and nothing else happens: no model call, no memory, no tool.
  The handler from that answer, never from the model or the message, is who the turn works for. Phase 3's
  AgentCore app only calls these two functions.
- **The graph** (`agent/copilot.py`): `remember` (look up the handler's memory for this message, shown as
  a card) → `model` → `tools` → back to `model`, or `confirm` → `tools`, or `save` → end. `confirm` holds
  the `interrupt()` before a sign-off; it checks in code that the handler may sign off that claim before
  it pauses, and refuses with the reason when they can't. At most 25 model calls a turn. `PostgresSaver` on
  the shared pool, with prepared statements off. The context is `CopilotContext` (the token, the handler,
  the conversation, the turn), read by each tool through `ToolRuntime`.
- **The tools**: the claims (queue, a claim in full with any sent-back decision, its steps), customers,
  policies, the latest dispatch, the evaluations, the settings; the context layer's seven lookups through
  the claim agent's route (the Gateway with only `undercover-context___*` tools, or straight to the
  context layer); `start_dispatch` (the settings' number of agents unless told), `start_evaluation`,
  `send_back`, `sign_off` (through `confirm`), `wait_for_run` and `check_success_criteria`. Each step is
  recorded with the app as it happens. A refusal from the app goes back to the model as text.
- **`check_success_criteria`**: starts the whole held-out set's evaluation, waits for it, then builds the
  three rows from its measures, the accuracy objective in Settings and `get_success_counts`. When the
  evaluation fails, stops or is still going after 15 minutes, the first two rows say so with no figures;
  the third is always there, met when nothing has been paid over the limit, with why.
- **Memory**: `AgentCoreMemoryStore` at `UNDERCOVER_COPILOT_MEMORY_ID` under `("handlers", handler_id)`,
  the id from the app's `get_signed_in_handler`. `save` stores the handler's message and the answer, and
  nothing else. Without the setting, both nodes do nothing.
- **The instructions** in `backend/instructions/copilot.md`: the goal, who it works for, what it can see
  and do, the three success criteria, how to answer, and "it can't do that yet, and here's where on the
  dashboard" for everything else.

Done when:

- [ ] A sign-in the app refuses ends the turn and its confirmation answer before any model call (the stub
      model records no calls), with the app's reason on the turn
- [ ] A stub-model test runs a lookup turn end to end through `TestClient(app)`, recording its steps
- [ ] The pause answered by a freshly built Copilot on the same saved state pays once and repeats nothing
- [ ] Cancel leaves the claim unpaid and asks what's next
- [ ] Marcus asking to sign off gets the reason, with no card
- [ ] A refusal from the app is passed on; the agent's key is never sent (a test checks the headers)
- [ ] The token isn't in the checkpoints, config, steps, turns, card, memory events, a failed turn's
      reason or the captured logs, after a turn, a pause and a failure
- [ ] `wait_for_run` makes no model call while it waits, and gives up at 15 minutes (with a stand-in clock)
- [ ] The success criteria from an evaluation's measures, from a failed evaluation, and with nothing paid
      over the limit yet
- [ ] The memory lookup and save use the signed-in handler's id as the actor and the conversation's id as
      the session, store only the message and answer, and do nothing without the setting
- [ ] `npm run check:python` passes; committed

## Phase 3: the Copilot as an AgentCore app

- **`agent/copilot_entrypoint.py`**: `BedrockAgentCoreApp`, one entrypoint with `start_turn`
  (conversation, turn, message, token), `answer_confirmation` (conversation, turn, answer, token) and
  `health`, calling Phase 2's two functions, which hold the sign-in gate. It answers at once and works the turn as an async task, completed in a `finally`.
  Nothing at import; ready on the first request, fetching only `OPENROUTER_API_KEY` and `DATABASE_URL` on
  Runtime. No key on the laptop.
- **`runtime/copilot.py`** runs it; `agentcore/agentcore.json` gets the runtime `copilot` beside `agent`,
  from the same package, with `runtime/copilot_policy.json` letting it call the Gateway and use the
  Copilot's Memory. Nothing else in `agentcore/` or `runtime/` changes.
- **`npm run dev`** serves it on 127.0.0.1:8081 with reload. `npm run check:runtime` imports both
  `runtime.main` and `runtime.copilot` with only the `runtime` group.

Done when:

- [ ] `TestClient` tests: nothing opens at import; a refused sign-in ends the turn before any model call;
      the ping is busy during a turn and healthy after; `health` answers `{"healthy": true}`
- [ ] `npm run check:runtime` passes for both files
- [ ] With `npm run dev` stopped, `uv run --env-file .env --isolated --only-group runtime python -m
      runtime.copilot` serves, and `{"operation": "health"}` answers `{"healthy": true}`
- [ ] `npm run check` passes; committed

## Phase 4: the sidebar

- **`dashboard/src/Copilot.tsx`** and its parts: a right-hand sidebar on every signed-in page, open the
  first time, collapsible to a slim tab with its own icon, remembered in the browser; over the page on a
  narrow window.
- Messages right, answers left, the answer as formatted markdown with claim ids linked; step cards with an
  icon for their kind, the tool's name and main input, working with a spinner and seconds, done with the
  time, refused or failed with the reason, a short entrance animation, opening to show what was asked and
  what came back (with a context-layer answer's file and owner); action cards stand out; a working
  indicator; the small-print line with model calls, cost and time.
- The confirmation card with Sign off and pay, and Cancel; the message box says to answer it first.
- The empty conversation greets the handler by first name with suggested questions. New conversation and
  the earlier conversations at the top; "Ask the Copilot" at the foot (Enter sends, Shift+Enter a new
  line, disabled during a turn) and "Signed in as".
- Polls about once a second only while a turn is working. Renews the sign-in when less than 20 minutes
  are left, before each message and answer. Light and dark, fonts and colors from `docs/BRAND.md`.

Done when:

- [ ] Vitest covers sending, the cards' three states, opening a card, the confirmation's two buttons, the
      blocked message box, collapsing and remembering, polling only while working, and the renewal
- [ ] The existing end-to-end tests pass with the sidebar open
- [ ] `npm run check:ts` passes; committed

## Phase 5: the end-to-end tests and the documents

- **A stand-in Copilot** (`e2e/copilot-stand-in.mjs`) plays a scripted turn through the app with the
  handler's sign-in: a lookup, an action, a refusal, a confirmation and a formatted answer.
- **`e2e/copilot.spec.ts`** drives the sidebar against it, signed in as Dana and as Marcus.
- **The documents**: `docs/SPEC.md` (the Copilot and the three success criteria), `docs/TECH_STACK.md`
  (the Copilot, its settings and Deployment with the second runtime and our RDS database),
  `docs/CODE_STANDARDS.md` (the Copilot as a part of its own), `docs/PROCESS.md` (port 8081, the
  stand-in) and `docs/STATUS.md`.

Done when:

- [ ] `npm run check` is green with `.env` moved out of the way
- [ ] The documents describe the product as it now is; committed

## Phase 6: the live checks

Run with `npm run dev`, on the default model, on the direct route and with memory off: blank
`UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_MEMORY_ID` and `UNDERCOVER_COPILOT_MEMORY_ID` in the shell for the
run. Stop Lab 1's `agentcore dev` first. Reseed once.

- [ ] Signed in as Dana Whitfield, in one conversation, the five handover questions, each answered in one
      turn
- [ ] Every figure in the last answer checked against the Scoreboard and the claims' pages
- [ ] The same conversation picked up after restarting `npm run dev`
- [ ] Signed in as Marcus Oyelaran, a request to sign off declined with the reason
- [ ] The sidebar checked in light and dark mode through the Playwright MCP server
- [ ] What the runs spent, from the costs in the database
- [ ] The Copilot answers its health check through `runtime/copilot.py` with only the `runtime` group
- [ ] Memory is checked on AWS by `week3day5_lab1.ipynb`, not here; `docs/STATUS.md` says so; committed

## Not for now

- Streaming answers word by word, or stopping a turn partway.
- The Copilot reading the claim agent's memory of customers.
- Sharing, renaming or deleting conversations.
- Changing settings, the configuration or improvement rounds through the Copilot.
- Langfuse tracing for the Copilot.
- Deploying: nothing here creates anything on AWS. `week3day5_lab1.ipynb` does that.

## The contract between the parts

Fixed before the build starts, so the backend, the dashboard and the Copilot can be built side by side.
All JSON. Times are ISO 8601 strings, money is a number of US dollars.

**Shapes**

- `Conversation`: `{id, title, created_at}`. The id is a UUID made by the app (36 characters).
- `ConversationDetail`: `Conversation` plus `turns: Turn[]`, oldest first.
- `Turn`: `{id, message, state, answer, model_calls, cost, seconds, card, error, started_at, answered_at,
  steps: Step[]}`. `id` is a number. `state` is `working`, `waiting_for_confirmation`, `done`, `failed`
  or `stopped` (shown, never stored). `answer`, `seconds`, `card`, `error` and `answered_at` may be null.
- `Card`: `{claim_id, customer_name, amount, reason}`, filled by the app from its records.
- `Step`: `{key, kind, tool, input, state, seconds, detail, started_at}`. `key` is the Copilot's own id for
  the step, unique in its turn. `kind` is `claims`, `customers`, `context`, `work`, `memory` or `action`.
  `state` is `working`, `done`, `refused` or `failed`. `detail` is null or `{asked, answer, file, owner}`,
  each a string or null, already trimmed by the Copilot to 2,000 characters.

**The handler's routes** (a signed-in handler; another handler's conversation is 404)

- `POST /api/conversations` `create_conversation`, no body → `Conversation`
- `GET /api/conversations` `list_conversations` → `Conversation[]`, newest first
- `GET /api/conversations/{conversation_id}` `get_conversation` → `ConversationDetail`
- `POST /api/conversations/{conversation_id}/messages` `send_message`, `{message}` → `Turn` (409 while a
  turn is working or a confirmation waits; the reason in `detail`)
- `POST /api/conversations/{conversation_id}/confirmation` `answer_confirmation`, `{answer}` with
  `sign_off` or `cancel` → `Turn` (409 when nothing waits)

**The Copilot's routes** (the handler's sign-in, never the key; only the handler's own turn, 409 unless
it is working)

- `POST /api/copilot/turns/{turn_id}/steps` `record_copilot_step`, a `Step` without `started_at` → `Step`.
  Recording a key again updates that step.
- `POST /api/copilot/turns/{turn_id}/pause` `pause_turn`, `{claim_id}` → `Turn`
- `POST /api/copilot/turns/{turn_id}/finish` `finish_turn`, `{answer, model_calls, cost, seconds}` → `Turn`
- `POST /api/copilot/turns/{turn_id}/fail` `fail_turn`, `{error, model_calls, cost, seconds}` → `Turn`

**The reads** (a signed-in handler, or the key where it says so)

- `GET /api/customers?name=` `find_customers` → `[{customer_id, name, email}]`
- `GET /api/customers/{customer_id}` `get_customer` → `{customer_id, name, email, policies, claims}`, the
  claims each `{claim_id, status, submitted_at, payout}`, earlier and in the queue
- `GET /api/runs/{run_id}` `get_run`: the key or a handler (it was the key's only)
- `GET /api/success-counts` `get_success_counts` → `{approval_limit, paid_over_limit, signed_off}`
- `GET /api/copilot/configuration` `get_copilot_configuration` → `{model, reasoning_effort, instructions}`
- `POST /api/dispatches` and `POST /api/evaluations` reply with `run_id` in their body.

**The app to the Copilot** (`backend/copilot_service.py`), each payload with its `operation`:

- `{operation: "start_turn", conversation_id, turn_id, message, token}`
- `{operation: "answer_confirmation", conversation_id, turn_id, answer, token}`
- `{operation: "health"}` → `{"healthy": true}`
