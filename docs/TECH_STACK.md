# Tech stack

- `backend/`: FastAPI and SQLAlchemy Core. With `DATABASE_URL` set it uses Postgres (on this machine, local Postgres in Docker, not Supabase) in the `undercover` schema, with at most 4 connections; without it, SQLite, which the tests use. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount. The payout rules are `backend/payout.py`, behind `work_out_payout`.
- `agent/`: the claim agent on OpenRouter, a hand-written LangGraph graph that waits for a handler's sign-off with `interrupt()` and pays as a step of its own. It saves its progress after every step with LangGraph's synchronous `PostgresSaver` in the same database, a saver of its own for each claim run on the process's shared pool of 4 connections. It records each model call's tokens and cost, from OpenRouter's `usage.cost`, and its working time. Each claim is traced in Langfuse, in a session named after its thread and tagged with its run.
- `agent/dispatcher.py`: the dispatcher, a LangGraph graph with no model. It lists a run's claims and fans out with one `Send` per claim, with `max_concurrency` set to N, through `work()` in `agent/main.py`, the only place it touches the claim agent. It works dispatches and evaluations alike.
- `agent/service.py`: the agent's own small FastAPI service on port 8002 (`UNDERCOVER_AGENT`). The backend calls it, through `backend/agent_service.py`, to start a dispatch or an evaluation and after each sign-off. The agent reaches claims, documents and its instructions only through the backend's API. It uses `openai/gpt-6-luna` with low reasoning effort by default, or the model in the environment variable `MODEL_NAME` if set, such as `openrouter/free`, with no reasoning effort.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.
