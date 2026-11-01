# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.

## The agent's parts

The agent has six parts:

7. **Tools** (`agent/main.py`): the claim agent's tools, which reach the backend only through its API.
8. **The claim graph** (`claim_graph` in `agent/main.py`): the model, its tools, the sign-off and the
   payment, saved after every step.
9. **The harness** (`run`, `carry_on` and `work` in `agent/main.py`): starts, carries on and resumes a
   claim, records its working time, and releases it when it fails.
10. **The dispatcher** (`agent/dispatcher.py`): works a dispatch or an evaluation with up to N claim
    agents at once. It touches the claim agent only through `work()`, so it can later call the agent in
    the cloud instead.
11. **The service** (`agent/service.py`): starts dispatches, evaluations and rounds, carries a round
    on from a handler's answer, and resumes signed-off claims, for the backend.
12. **The improver** (`agent/improver.py`): follows its recipe to propose new versions and recipes,
    pausing for a handler. It reaches the backend only through `agent/improver_api.py`.
