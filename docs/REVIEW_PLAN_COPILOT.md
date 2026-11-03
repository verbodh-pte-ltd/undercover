# Review of docs/PLAN_COPILOT.md

## Overall assessment

The plan is directionally strong and follows the project’s planning style: it breaks the work into six phases, uses checkbox-based acceptance criteria, and recognises the key technical constraints from the brief. It is also structured well enough to be implemented in order.

However, it is not yet fully compliant with the brief in `docs/COPILOT.md`. The most important issue is that the plan diverges from the brief in several core architecture decisions, especially around where conversation state lives, which database is used, and how the Copilot should authenticate to the app. Those issues are large enough that they should be corrected before implementation begins.

## What the plan does well

- Good phase breakdown with sensible sequencing.
- Includes explicit tests and verification steps for each phase.
- Recognises the key Copilot concerns: sign-in passing through, confirmation pause, state tracking, memory, and side-panel UI.
- Marks the right integration points with the app, runtime, and deployment work.
- Keeps the “brief wins over notebook differences” rule in view.

## Main compliance issues

### 1. It conflicts with the brief on state persistence and database ownership

This is the largest risk.

The brief is explicit that the Copilot’s state is kept with the conversation in the app and is persisted in Supabase, and that the sidebar reads state from the app rather than a separate store. In particular:

- “Each conversation’s state is saved after every step in our Supabase database…”
- “The steps the sidebar shows are kept in our app with their conversation…”
- “The app knows where each turn stands: working, waiting for a confirmation, done or failed…”
- “The sign-in goes from the dashboard to our app to the Copilot with each message and each answer to a confirmation. It is never saved…”

The plan instead defines its own local tables (`conversations`, `turns`, `copilot_steps`) in `backend/store.py`, with a backend-owned persistence model and then says the app owns the turn state. That makes the data path different from the brief and is not simply a small implementation choice: it changes the architecture, ownership, and how the sidebar and runtime sessioning behave.

This is a direct implementation risk because the brief explicitly says the Copilot should use the general app state model and Supabase-backed conversation persistence. The plan’s proposed SQL tables do not match that contract.

### 2. It conflicts with the brief on the runtime database choice

The plan says:

- “Our database on AWS is RDS, not Supabase.”
- “The Copilot’s `DATABASE_URL` on Runtime is the RDS database, straight to port 5432 with TLS …”

But the brief says:

- `DATABASE_URL` is the Supabase transaction pooler, port 6543
- the Copilot’s Memory is created by the deployment notebook, not by code
- the runtime role may call the Gateway and use the Copilot’s Memory
- the deployment config includes `UNDERCOVER_COPILOT_MEMORY_ID` and `UNDERCOVER_COPILOT`

This is a major mismatch. It changes both the deployment model and the runtime environment. If implemented as written, the Copilot would not match the project’s chosen Azure/AWS runtime configuration in the brief.

### 3. It weakens the security model in a way the brief explicitly forbids

The brief is very clear:

- every call made by the Copilot carries the handler’s sign-in
- it never sends the agent API key
- the app checks whether the signer is valid before the model is called
- the sign-in is never saved in the conversation state, steps, memory, or logs
- the app treats any request carrying the agent key as the agent’s request, no matter what else is attached

The plan does cover some of this, but it does so as a side constraint rather than a first-class design, and it is not spelled out strongly enough in the critical phases. The plan says the Copilot “passes the token through” but also introduces a local, separate conversation store. That means the token could be accidentally persisted unless the plan explicitly forbids it at each store boundary. The brief requires this to be treated as a hard security requirement, not just a test after the fact.

### 4. It narrows the middleware and app/service contract too much without reconciling it to the brief

The brief says the Copilot is a second AgentCore app beside the claim agent, built the same way as the claim agent app, with one entrypoint and each turn’s work registered as an async task. It also says the app starts each turn and each answer to a confirmation with the conversation’s id as the runtime session id, then responds immediately while the work runs in the background. The plan does do some of this, but it chooses a local `backend/copilot_service.py` and local app-owned routes that are presented as the contract. This is too much design freedom without validating it against the brief’s runtime contract.

The risk here is not just naming; it is the fact that the brief’s runtime sessioning, async task execution, and app-owned state are all tightly coupled. The plan needs to defend these contracts explicitly before coding.

### 5. The state-machine model is not precise enough for the brief’s required turn states

The brief defines turn states and confirmation-handling very specifically:

- working
- waiting for a confirmation
- done
- failed
- stopped (after 20 minutes or after a confirmation answer)
- waiting confirmation stays attached to the conversation until the handler answers

The plan’s `turns` state field includes `working`, `waiting`, `done`, `failed`, `stopped`, but does not surface the explicit and essential distinction of “waiting for a confirmation” as a first-class state and a separate workflow. That matters because a confirmation is a pause in a turn, not a generic wait state. The brief calls out this difference as part of how the sidebar should behave and when the app may accept a new message.

This is a planning gap rather than a bug, but it is a real risk to the runtime flow and the sidebar behavior.

### 6. The plan under-specifies the exact security and authorization checks required by the brief

The brief requires all of the following before the model is called:

- ask the app who is signed in
- verify that sign-in is still accepted
- end the turn immediately if not accepted
- reject a handler who cannot sign off over the limit
- never expose the model’s answer as a sign-off authorization decision
- refuse any request carrying the agent’s key as the agent’s identity regardless of other headers

The plan discusses tests for token persistence and a refused sign-in, but the controlling logic isn’t emphasized enough in the phase sequence. The security checks need to be treated as a hard gate at the start of every turn and confirmation. This should be a required phase gate, not just a later test.

### 7. The plan assumes “this plan builds on runtime build” without reconciling it with the brief’s deployment sequencing

The plan starts with “This plan builds on the runtime build (`docs/PLAN_RUNTIME.md`) … Start Phase 1 only once all five runtime phases are ticked.”

That may be reasonable if the runtime work truly is necessary. But the brief says the Copilot is a second runtime app beside the claim agent and that the deployment notebook creates the Copilot runtime, with one runtime package and a second app of the same style. The plan should be explicit that this is an implementation dependency rather than an architecture requirement, or else it will create confusion about whether the Copilot is supposed to be built independently or as part of the runtime sequence.

## Specific risks that are likely to cause rework

1. Database ownership mismatch (Supabase-backed app state vs app-local tables)
2. AWS environment mismatch (RDS vs Supabase transaction pooler)
3. Token persistence and authorization regression risk
4. Confirmation-pause state not modeled precisely enough
5. Runtime sessioning and memory lifecycle not tied tightly enough to the conversation id contract
6. UI and app contract mismatch around what the sidebar reads vs what the app stores
7. Deployment config mismatch for `UNDERCOVER_COPILOT_MEMORY_ID`, `UNDERCOVER_COPILOT`, and runtime package layout

## Recommended changes before implementation

1. Rebase the plan to the brief’s architecture, not to the notebook or the running runtime implementation.
2. Make the app-owned state contract the primary design artifact. The Copilot must pass through the handler’s sign-in and never persist it.
3. Replace the local `conversations/turns/copilot_steps` model with the app’s conversation/turn state model as described in the brief.
4. Remove the RDS-first design and align the deployment config to the brief’s Supabase + AgentCore settings.
5. Explicitly add a dedicated security phase before the graph work, with tests for:
   - signed-in handler check on each turn
   - no token persistence
   - no agent-key forwarding
   - a handler without sufficient authority is refused before any model call
6. Tighten the turn-state model: `working`, `waiting_for_confirmation`, `done`, `failed`, `stopped`, with the always-saved confirmation card and resumption semantics spelled out.
7. Add a phase to verify the exact runtime sessioning and memory contract from the brief: session id = conversation id, memory namespace by handler id, skip memory when setting is absent.
8. Keep the UI phase tied to the app’s state model, not to an invented app-local store.

## Conclusion

The plan is a solid engineering plan in format and budget, but it is not yet safe to implement as-is. It needs a re-alignment pass to remove architecture drift from the brief, especially around persistent state, the database environment, and the security checks on the handler’s identity. The plan is close, but not yet compliant enough to be treated as a faithful implementation plan for `docs/COPILOT.md`.
