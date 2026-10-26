# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount. The backend enforces a run's cap, retries and the missed-claims check.
- `agent/`: two LangChain `create_agent` agents on OpenRouter, with no other agent framework, in three modules:
  - `backend.py` is the agents' one way to reach the backend's API. The address comes from `UNDERCOVER_API` (default `http://127.0.0.1:8000`), the same variable the dashboard uses.
  - `tools.py` holds every tool, each a thin call to the API, except `work_claim`, which runs the claim agent on one claim and catches its failures. Tools get their claim or run from the agent's context (`context_schema` with `ToolRuntime`), never from the model.
  - `harness.py` holds the model, the middleware and both agents.
    - **The queue agent** (`run_queue(cap)`) plans a run, keeps its to-do list and hands each claim to the claim agent with `work_claim`. `run_one(claim_id)` works one named claim.
    - **The claim agent** works one claim from a clean start and reports back in a line.
    - **Middleware:** `ModelRetryMiddleware` retries model errors, `ModelCallLimitMiddleware` caps the queue agent's calls, a `StepLog` middleware logs the claim agent's steps on the claim, and an `after_model` hook reminds the queue agent if it stops before finishing.
    - **The model:** `deepseek/deepseek-v4.1-flash` with low reasoning effort by default, or the model in `MODEL_NAME` if set, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.
