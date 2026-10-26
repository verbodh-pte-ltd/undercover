# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Short modules and short functions with clear names. Split a function once it does more than one job.
8. Every public function has a one-line docstring saying what it does.

## Structure

9. No hard-coded addresses. A URL, host, port or file path comes from an environment variable with a local default, set in one place, and the same name is used everywhere: `UNDERCOVER_API` for the backend's address, `UNDERCOVER_DB` for the database.
10. One way to reach each service. The agent reaches the backend only through `agent/backend.py`, and the dashboard only through `dashboard/src/api.ts`. Nothing else opens a connection. Never decide what to do by parsing an error string meant for a model or a person; check the status.
11. Keep the layers apart, each in one place:
    - Business rules (statuses, the cap, retries, sign-off, what counts as missed) live in the backend.
    - Every agent tool lives in `agent/tools.py`, as a thin call to the backend's API. The one exception is `work_claim`, which runs the claim agent; it catches failures because one bad claim must never stop a run.
    - The harness (the model, the middleware, building and running the agents) lives in `agent/harness.py`.
    - One concern is not spread over several modules, and one module does not mix two.
12. Use the framework before writing your own. Before writing a loop, a closure, a parser or a wrapper around an agent, check what the installed version of LangChain already provides, and use that:
    - `context_schema` with `ToolRuntime` for run state;
    - middleware hooks such as `wrap_tool_call` and `after_model` with `jump_to`;
    - the built-in middleware.
13. Agent state goes through the agent's context or state, never through closures or globals. A tool takes from the model only what the model has to decide. For example, the claim a claim agent works comes from its context.
14. Fix a type error by typing the code correctly, not by loosening the check. Say why in one line if a cast is the only way.

## Agent instructions

15. Instructions give the agent its goal, what a good result looks like, the rules it must keep and where to find what it needs. They are not step-by-step scripts of which tool to call when. Rules that must always hold are enforced in code (the backend or the harness), and the instructions only say they exist.
16. Write instructions from the policy wording and the claims manual, never from `data/evals/`.
