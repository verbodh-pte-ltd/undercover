# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.

## The agent's parts

The agent has six parts:

7. **Tools** (`agent/main.py`): the claim agent's tools, which reach the backend only through its API,
   with `backend()` in `agent/main.py` as their client. That client has no call to the scores, the
   handlers' decisions, the rounds or the settings, and a test checks it.
8. **The claim graph** (`claim_graph` in `agent/main.py`): the check for instructions hidden in the
   claim (its first node, on a second model, outside the versioned configuration), then the model, its
   tools, the sign-off and the payment of a claim within the limit, saved after every step.
9. **The harness** (`run`, `carry_on` and `work` in `agent/main.py`): starts, carries on and resumes a
   claim, records its working time, and releases it when it fails.
10. **The dispatcher** (`agent/dispatcher.py`): works a dispatch or an evaluation with up to N claim
    agents at once. It touches the claim agent only through `work()`, so it can later call the agent in
    the cloud instead.
11. **The service** (`agent/service.py`): starts dispatches, evaluations and rounds, carries a round
    on from a handler's answer, and resumes signed-off claims, for the backend.
12. **The improver** (`agent/improver.py`): follows its recipe to propose new versions and recipes,
    pausing for a handler. Its instructions are goals that wrap the recipe, which is data it follows.
13. **The improver's client** (`agent/improver_api.py`): the only way to the round, the improver's
    view of the results and its proposals. The claim agent never imports it.

## The dashboard

14. **The Supabase client** (`dashboard/src/supabase.ts`): the dashboard's one Supabase client, used
    only for Supabase Auth: signing in and out and the session's access token. The dashboard sends
    that token with each action to the backend, and never reads data from Supabase. It is made on
    first use, through `dashboard/src/auth.ts`, which signs in through Cognito instead when the
    build has `VITE_COGNITO_USER_POOL_ID` and `VITE_COGNITO_CLIENT_ID`.
