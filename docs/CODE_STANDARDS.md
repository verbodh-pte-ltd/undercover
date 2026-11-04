# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.

## The agent's parts

The agent has six parts:

7. **Tools** (`agent/main.py`): the claim agent's tools, thin layers that take no id the agent
   already knows and reach the app and the context layer through one route, picked when a claim
   graph is built, with one module for each: `agent/gateway.py` (the AgentCore Gateway, signed) and
   `agent/direct.py` (the app with the key, and the context layer unsigned), sharing
   `agent/mcp_client.py`. Everything else the agent sends the backend goes through `backend()` in
   `agent/main.py`. Neither has a call to the scores, the handlers' decisions, the rounds or the
   settings, and a test checks it. The agent's memory of each customer is `agent/memory.py`, used only
   by the claim graph's `remember` node and its `search_memory` tool.
8. **The claim graph** (`claim_graph` in `agent/main.py`): the check for instructions hidden in the
   claim (its first node, on a second model, outside the versioned configuration), then the model, its
   tools, the sign-off and the payment of a claim within the limit, saved after every step.
9. **The harness** (`run`, `carry_on` and `work` in `agent/main.py`): starts, carries on and resumes a
   claim, records its working time, and releases it when it fails.
10. **The dispatcher** (`agent/dispatcher.py`): works a dispatch or an evaluation with up to N claim
    agents at once. It touches the claim agent only through `work()`, so it can later call the agent in
    the cloud instead.
11. **The AgentCore app** (`agent/entrypoint.py`): one entrypoint that takes each payload from the
    backend, which names an operation and its inputs, checks it, answers at once, and works on a
    thread registered with the app until the work ends: it works the runs and rounds the backend has
    created, by id, carries a round on from the review the backend recorded, and resumes signed-off
    claims. The backend, not the agent, keeps one run or round going at a time. Nothing happens when
    it's imported; it gets ready on its first request. The same app runs on the laptop and, through
    `runtime/main.py`, on AgentCore Runtime.
12. **The improver** (`agent/improver.py`): follows its recipe to propose new versions and recipes,
    pausing for a handler. Its instructions are goals that wrap the recipe, which is data it follows.
13. **The improver's client** (`agent/improver_api.py`): the only way to the round, the improver's
    view of the results and its proposals. The claim agent never imports it.

## The context layer

15. **The context layer** (`context/server.py`): its own service and package, which reads only its
    own files, once, writes none, needs no environment variables and imports nothing from the app or
    the agent. Every tool is read-only, with a one-line docstring and no optional inputs, and every
    answer names its file and source.

## The dashboard

16. **The Supabase client** (`dashboard/src/supabase.ts`): the dashboard's one Supabase client, used
    only for Supabase Auth: signing in and out and the session's access token. The dashboard sends
    that token with each action to the backend, and never reads data from Supabase. It is made on
    first use, through `dashboard/src/auth.ts`, which signs in through Cognito instead when the
    build has `VITE_COGNITO_USER_POOL_ID` and `VITE_COGNITO_CLIENT_ID`.

## The Copilot

The Copilot is a part of its own, with the same rules as the others. Its code lives in the `agent`
package beside the claim agent's, and reuses the claim agent's code where it fits: OpenRouter, the
route to the context layer, the shared pool and fetching its credentials from AgentCore Identity.

17. **The graph and its tools** (`agent/copilot.py`): a LangGraph graph written by hand, `remember`,
    `model`, `tools`, `confirm` and `save`, saved after every step. The handler's sign-in reaches the
    graph and its tools only in its context, never in the config, the saved state, a step, the memory,
    an error or a log. Each turn and each answer to a confirmation starts by asking the app who is
    signed in, and the handler is whoever the app says, never a name from the model or the message.
    Every figure the Copilot gives, the confirmation card and the success criteria included, is worked
    out in code or read from the app, never by the model. Only the sign-off pauses, in `confirm`, a
    node of its own that only reads before its `interrupt()`.
18. **The Copilot's client** (`agent/copilot_api.py`): the only way to the app, with the handler's
    sign-in. It never uses `backend()` in `agent/main.py` and never reads `UNDERCOVER_API_KEY`, so it
    can't send the agent's key, and a test checks it. A refusal from the app goes back to the model as
    text, with the app's reason.
19. **The Copilot's AgentCore app** (`agent/copilot_entrypoint.py`): one entrypoint that checks each
    payload from the app, answers at once and works the turn on a thread registered with the app.
    Nothing happens when it's imported; it gets ready on its first request. The same app runs on the
    laptop and, through `runtime/copilot.py`, on AgentCore Runtime.
20. **The app's side** (`backend/copilot_service.py` and the conversation routes in `backend/main.py`):
    the only calls from the app to the Copilot. The app owns each turn's state, its steps and a
    waiting card, which the Copilot writes only through the app's routes, and the sidebar reads only
    the app. Neither the payload nor the sign-in is ever logged or put in an error.
21. **The sidebar** (`dashboard/src/Copilot.tsx` and its parts): reads and writes only through the app,
    with the handler's sign-in, like every other page.
